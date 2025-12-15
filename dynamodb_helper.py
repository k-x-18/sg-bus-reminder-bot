"""
DynamoDB helper functions for storing and retrieving user reminders.
"""
import os
import boto3
import uuid
from typing import List, Dict, Optional
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)

# DynamoDB configuration
DYNAMODB_TABLE_NAME = os.getenv('DYNAMODB_TABLE_NAME', 'sg-bus-reminders')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)


def get_table():
    """Get the DynamoDB table instance."""
    return dynamodb.Table(DYNAMODB_TABLE_NAME)


def ensure_table_exists():
    """
    Ensure the DynamoDB table exists. Creates it if it doesn't.
    This should be called at startup.
    """
    table = get_table()
    try:
        # Try to describe the table to check if it exists
        table.load()
        logger.info(f"DynamoDB table {DYNAMODB_TABLE_NAME} exists")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.info(f"Creating DynamoDB table {DYNAMODB_TABLE_NAME}...")
            try:
                table = dynamodb.create_table(
                    TableName=DYNAMODB_TABLE_NAME,
                    KeySchema=[
                        {
                            'AttributeName': 'reminder_id',
                            'KeyType': 'HASH'  # Partition key
                        }
                    ],
                    AttributeDefinitions=[
                        {
                            'AttributeName': 'reminder_id',
                            'AttributeType': 'S'
                        },
                        {
                            'AttributeName': 'chat_id',
                            'AttributeType': 'N'
                        }
                    ],
                    GlobalSecondaryIndexes=[
                        {
                            'IndexName': 'chat_id-index',
                            'KeySchema': [
                                {
                                    'AttributeName': 'chat_id',
                                    'KeyType': 'HASH'
                                }
                            ],
                            'Projection': {
                                'ProjectionType': 'ALL'
                            },
                            'ProvisionedThroughput': {
                                'ReadCapacityUnits': 5,
                                'WriteCapacityUnits': 5
                            }
                        }
                    ],
                    BillingMode='PAY_PER_REQUEST'  # Use on-demand pricing
                )
                # Wait for table to be created
                table.wait_until_exists()
                logger.info(f"DynamoDB table {DYNAMODB_TABLE_NAME} created successfully")
            except Exception as create_error:
                logger.error(f"Failed to create DynamoDB table: {create_error}")
                raise
        else:
            logger.error(f"Error checking DynamoDB table: {e}")
            raise


def get_user_reminders(chat_id: int) -> List[Dict]:
    """
    Get all reminders for a user by chat_id.
    Returns a list of reminder dictionaries.
    """
    table = get_table()
    try:
        response = table.query(
            IndexName='chat_id-index',
            KeyConditionExpression='chat_id = :chat_id',
            ExpressionAttributeValues={
                ':chat_id': chat_id
            }
        )
        reminders = response.get('Items', [])
        # Convert DynamoDB format to regular dict format
        result = []
        for item in reminders:
            result.append({
                'reminder_id': item['reminder_id'],
                'bus_number': item.get('bus_number', ''),
                'bus_stop': item.get('bus_stop', ''),
                'bus_stop_name': item.get('bus_stop_name', ''),
                'days': item.get('days', ''),
                'time': item.get('time', '')
            })
        return result
    except ClientError as e:
        logger.error(f"Error getting reminders for chat_id {chat_id}: {e}")
        return []


def add_reminder(chat_id: int, bus_number: str, bus_stop: str, 
                 bus_stop_name: str, days: str, time: str) -> Optional[str]:
    """
    Add a new reminder to DynamoDB.
    Returns the reminder_id if successful, None otherwise.
    """
    table = get_table()
    reminder_id = str(uuid.uuid4())
    try:
        table.put_item(
            Item={
                'reminder_id': reminder_id,
                'chat_id': chat_id,
                'bus_number': bus_number,
                'bus_stop': bus_stop,
                'bus_stop_name': bus_stop_name,
                'days': days,
                'time': time
            }
        )
        logger.info(f"Added reminder {reminder_id} for chat_id {chat_id}")
        return reminder_id
    except ClientError as e:
        logger.error(f"Error adding reminder: {e}")
        return None


def delete_reminder(reminder_id: str) -> bool:
    """
    Delete a reminder by its ID.
    Returns True if successful and item was deleted, False otherwise.
    """
    if not reminder_id:
        logger.error("Cannot delete reminder: reminder_id is empty or None")
        return False
    
    table = get_table()
    try:
        # Use ReturnValues to check if item actually existed and was deleted
        response = table.delete_item(
            Key={
                'reminder_id': reminder_id
            },
            ReturnValues='ALL_OLD'
        )
        
        # Check if an item was actually deleted
        if 'Attributes' in response and response['Attributes']:
            logger.info(f"Deleted reminder {reminder_id}")
            return True
        else:
            logger.warning(f"Reminder {reminder_id} not found in database")
            return False
    except ClientError as e:
        logger.error(f"Error deleting reminder {reminder_id}: {e}")
        return False


def get_all_reminders() -> List[Dict]:
    """
    Get all reminders from the table (for checking scheduled reminders).
    This scans the entire table, so use sparingly.
    """
    table = get_table()
    try:
        response = table.scan()
        reminders = response.get('Items', [])
        result = []
        for item in reminders:
            result.append({
                'reminder_id': item['reminder_id'],
                'chat_id': int(item['chat_id']),
                'bus_number': item.get('bus_number', ''),
                'bus_stop': item.get('bus_stop', ''),
                'bus_stop_name': item.get('bus_stop_name', ''),
                'days': item.get('days', ''),
                'time': item.get('time', '')
            })
        return result
    except ClientError as e:
        logger.error(f"Error scanning reminders: {e}")
        return []


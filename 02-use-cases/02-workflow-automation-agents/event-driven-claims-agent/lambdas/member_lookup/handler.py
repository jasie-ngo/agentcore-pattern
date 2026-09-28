import os
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(
    os.environ.get("HESTA_MEMBERS_TABLE", "Hesta-members")
)

def handler(event, context):

    member_id = event.get("member_id")
    email = event.get("email")

    if member_id:
        response = table.get_item(
            Key={"member_id": member_id}
        )
        return response.get("Item", {})

    if email:
        response = table.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email)
        )

        items = response.get("Items", [])
        if items:
            return items[0]

        return {"error": "Member not found"}

    return {"error": "member_id or email required"}
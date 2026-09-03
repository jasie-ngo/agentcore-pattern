import os
import uuid
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")

table = dynamodb.Table(
    os.environ.get("HESTA_CASES_TABLE", "Hesta-cases")
)

def handler(event, context):
    member_id = event.get("member_id")

    if not member_id:
        return {"error": "member_id required"}

    try:
        # Query cases by member_id GSI
        response = table.query(
            IndexName="member_id-index",
            KeyConditionExpression=Key("member_id").eq(member_id)
        )

        items = response.get("Items", [])

        # Existing case(s) found
        if items:
            return {
                "status": "existing_cases_found",
                "cases": items
            }

        # No cases found - create one
        case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"

        new_case = {
            "case_id": case_id,
            "member_id": member_id,
            "status": "Open"
        }

        table.put_item(Item=new_case)

        return {
            "status": "new_case_created",
            "case": new_case
        }

    except Exception as e:
        return {"error": str(e)}
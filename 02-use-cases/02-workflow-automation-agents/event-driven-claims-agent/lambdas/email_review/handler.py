import os
import uuid
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")

table = dynamodb.Table(
    os.environ.get("HESTA_HUMANREVIEW_TABLE", "Hesta-humanreview")
)

def handler(event, context):
    case_id = event.get("case_id")

    if not case_id:
        return {
            "error": "case_id required"
        }

    try:
        review_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()

        item = {
            "review_id": review_id,
            "case_id": case_id,
            "action": "review draft",
            "review_type": "Draft email",
            "draft_subject": event.get("draft_subject", ""),
            "draft_body": event.get("draft_body", ""),
            "escalation_reasons": event.get("escalation_reasons", ""),
            "created_at": timestamp,
            "status": "pending_review"
        }

        table.put_item(Item=item)

        return {
            "success": True,
            "review_id": review_id,
            "case_id": case_id
        }

    except Exception as e:
        return {
            "error": str(e)
        }
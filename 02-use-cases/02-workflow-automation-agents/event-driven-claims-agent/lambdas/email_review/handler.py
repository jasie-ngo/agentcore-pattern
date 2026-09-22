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
            "revision_count": event.get("revision_count", 0),
            "created_at": timestamp,
            "status": "pending_review"
        }
        if event.get("attachment_status"):
            item["attachment_status"] = event["attachment_status"]
        if event.get("attachment_notes"):
            item["attachment_notes"] = event["attachment_notes"]
        if event.get("form_id"):
            item["form_id"] = event["form_id"]
        if event.get("form_name"):
            item["form_name"] = event["form_name"]
        if event.get("missing_fields"):
            item["missing_fields"] = event["missing_fields"]
        if event.get("received_filenames"):
            item["received_filenames"] = event["received_filenames"]
        if event.get("review_result"):
            item["review_result"] = event["review_result"]

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
"""Scheduled work (every hour between 06:00 and 23:00, for each organization inside its own tenant context):
remind the departments that have not signed a clearance form for 3 days."""
from scheduler import register_job


@register_job(minutes=60)
def clearance_reminders(app, tenant):
    from services import clearance as wf
    for form in wf.due_reminders(days=3):
        try:
            wf.remind(form)
        except wf.WorkflowError:
            continue

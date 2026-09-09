"""Patient-scoped, text-only records for the assistant."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ExamPlan, ExamInfo, HospitalInfo, PlanExecutionDetail, UserStatusInfo
from .serializers import iso


def read_agent_records(db: Session, user_id: str) -> dict:
    plans = list(db.scalars(select(ExamPlan).where(ExamPlan.user_id == user_id)
        .order_by(ExamPlan.generate_time.desc(), ExamPlan.plan_id).limit(21)))
    result = {'source': '当前登录患者的体检记录', 'visits': [], 'truncated': len(plans) > 20}
    used = 0
    for plan in plans[:20]:
        hospital = db.get(HospitalInfo, plan.hospital_id)
        status = db.get(UserStatusInfo, plan.record_id) if plan.record_id else None
        profile = status.profile_data or {} if status else {}
        visit = {'date': iso(plan.generate_time), 'hospital': hospital.hospital_name if hospital else '',
                 'status': plan.plan_status, 'simulated': bool(profile.get('demoImport')), 'exams': []}
        rows = db.execute(select(PlanExecutionDetail, ExamInfo)
            .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
            .where(PlanExecutionDetail.plan_id == plan.plan_id)
            .order_by(PlanExecutionDetail.step_order).limit(101)).all()
        if len(rows) > 100:
            result['truncated'] = True
        for detail, exam in rows[:100]:
            report = detail.exam_report
            row = {'name': exam.item_name, 'status': detail.exec_status,
                   'completedAt': iso(detail.actual_end), 'report': None}
            if isinstance(report, dict):
                row['report'] = {key: report.get(key) for key in ('conclusion', 'reportedAt', 'items')}
            size = len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            if used + size > 60_000:
                result['truncated'] = True
                continue
            used += size
            visit['exams'].append(row)
        result['visits'].append(visit)
    return result

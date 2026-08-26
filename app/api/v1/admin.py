from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.session_auth import COOKIE, create_session, require_admin_session, verify_password
from app.config.settings import settings
from app.database.connection import get_db
from app.models.operations import MovieRequest, DataQualityIssue, OttEvidence, NotificationLog, OperationState

router=APIRouter(prefix="/api/v1/admin",tags=["Admin"])
class Login(BaseModel): password:str=Field(min_length=8,max_length=512)
class RequestStatus(BaseModel): status:str=Field(pattern="^(PENDING|REVIEWING|FOUND|ADDED|REJECTED)$")
@router.post("/login")
def login(payload:Login,response:Response):
    if not verify_password(payload.password): raise HTTPException(401,"Invalid credentials")
    response.set_cookie(COOKIE,create_session(),httponly=True,secure=settings.ENVIRONMENT == "production",samesite="strict",max_age=28800,path="/")
    return {"authenticated":True}
@router.post("/logout")
def logout(response:Response): response.delete_cookie(COOKIE,path="/");return {"authenticated":False}
@router.get("/session")
def session(_:None=Depends(require_admin_session)): return {"authenticated":True}
@router.get("/dashboard")
def dashboard(db:Session=Depends(get_db),_:None=Depends(require_admin_session)):
    return {"open_issues":db.query(DataQualityIssue).filter(DataQualityIssue.resolved_at.is_(None)).count(),"requests":db.query(MovieRequest).filter(MovieRequest.status=="PENDING").count(),"ott_queue":db.query(OttEvidence).filter(OttEvidence.status.in_(["QUEUED","CONFLICTING","FAILED"])).count(),"notifications":db.query(NotificationLog).count()}
@router.get("/requests")
def requests(status:str|None=None,db:Session=Depends(get_db),_:None=Depends(require_admin_session)):
    q=db.query(MovieRequest)
    if status:q=q.filter(MovieRequest.status==status)
    return [{"request_id":x.request_id,"movie_name":x.movie_name,"release_year":x.release_year,"language":x.language,"details":x.details,"status":x.status,"created_at":x.created_at} for x in q.order_by(MovieRequest.created_at.desc()).limit(200)]
@router.patch("/requests/{request_id}")
def update_request(request_id:str,payload:RequestStatus,db:Session=Depends(get_db),_:None=Depends(require_admin_session)):
    item=db.query(MovieRequest).filter_by(request_id=request_id).first()
    if not item:raise HTTPException(404,"Request not found")
    item.status=payload.status;db.commit();return {"request_id":item.request_id,"status":item.status}

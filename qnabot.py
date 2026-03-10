import os
import re
import requests
from datetime import datetime
from slack_bolt import App
from slack_sdk import WebClient
from slack_bolt.adapter.socket_mode import SocketModeHandler
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from ingest import process_and_save_to_db, get_vector_db

# 1. 초기화 (환경변수 체크 필수!)
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))
client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

if not os.path.exists("temp"):
    os.makedirs("temp")

def is_channel_private(channel_id):
    try:
        res = client.conversations_info(channel=channel_id)
        return res["channel"].get("is_private", False) or res["channel"].get("is_im", False)
    except:
        return True

# --- [공통 로직] 파일 다운로드 및 DB 저장 ---
SUPPORTED_EXTENSIONS = {'.pdf', '.pptx', '.docx', '.txt', '.md', '.xlsx', '.csv', '.hwpx'}

def download_and_ingest(file_id, file_name, file_url, channel_id, created_at):
    is_locked = is_channel_private(channel_id)
    local_path = f"temp/{file_name}"
    
    # 1. 확장자 체크 강화
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        if ext == ".hwp":
            msg = f"🚫 `{file_name}`: HWP는 아직 읽을 수 없어요. PDF로 변환해서 올려주시면 감사하겠습니다!"
        else:
            msg = f"❓ `{file_name}`: `{ext}`는 지원하지 않는 파일 형식입니다.\n(지원: PDF, PPTX, DOCX, HWPX, TXT, MD, XLSX, CSV)"
        client.chat_postMessage(channel=channel_id, text=msg)
        return False

    status_msg = client.chat_postMessage(channel=channel_id, text=f"⚙️ `{file_name}` 분석을 시작합니다... (추출 및 색인 중)")

    try:
        # 2. 다운로드 시도
        token = os.environ.get("SLACK_BOT_TOKEN")
        response = requests.get(file_url, headers={'Authorization': f'Bearer {token}'})
        
        if response.status_code == 200:
            with open(local_path, "wb") as f:
                f.write(response.content)
            
            # 3. DB 등록 (ingest.py에서 상세 메시지를 success, detail_msg로 받아옴)
            success, detail_msg = process_and_save_to_db(
                local_path, file_id, channel_id, is_locked, file_url, created_at
            )
            
            if success:
                client.chat_update(channel=channel_id, ts=status_msg['ts'], 
                                 text=f"✅ `{file_name}` 등록 완료!\n- {detail_msg}")
                return True
            else:
                # 텍스트 추출 실패 등의 사유를 상세히 표시
                client.chat_update(channel=channel_id, ts=status_msg['ts'], 
                                 text=f"❌ `{file_name}` 분석 실패\n- 사유: {detail_msg}")
        else:
            # 4. 다운로드 실패 (주로 권한 문제)
            client.chat_update(channel=channel_id, ts=status_msg['ts'], 
                             text=f"❌ `{file_name}` 다운로드 실패\n- Slack 권한 문제일 수 있습니다. (HTTP {response.status_code})")
            
    except Exception as e:
        print(f"Error: {e}")
        client.chat_update(channel=channel_id, ts=status_msg['ts'], 
                         text=f"⚠️ `{file_name}` 처리 중 내부 오류가 발생했습니다. (관리자에게 문의하세요)")
    
    return False

# --- [이벤트 핸들러] ---
@app.event("member_joined_channel")
def handle_bot_join(event):
    bot_id = client.auth_test()["user_id"]
    if event["user"] == bot_id:
        channel_id = event["channel"]
        client.chat_postMessage(channel=channel_id, text="👋 초대 감사드립니다! 기존 파일들을 동기화합니다.")
        
        try:
            result = client.files_list(channel=channel_id, count=20)
            files = result.get("files", [])
            for f in files:
                created_at = datetime.fromtimestamp(f["created"]).isoformat()
                download_and_ingest(f["id"], f["name"], f.get("url_private_download"), channel_id, created_at)
        except Exception as e:
            print(f"Sync error: {e}")

@app.event("file_shared")
def handle_file_shared(event):
    file_id = event["file_id"]
    channel_id = event["channel_id"]
    f_info = client.files_info(file=file_id).get("file")
    created_at = datetime.fromtimestamp(f_info.get("created")).isoformat()

    download_and_ingest(file_id, f_info["name"], f_info["url_private_download"], channel_id, created_at)

def smart_search_and_answer(query, channel_id):
    """검색 및 답변 로직에서도 client를 사용합니다."""
    try:
        status = client.chat_postMessage(channel=channel_id, text=f"🔍 `{query}` 탐색 중...")
        
        is_locked = is_channel_private(channel_id)
        search_filter = {"channel_id": {"$in": [channel_id, "public"]}} if is_locked else {"channel_id": "public"}

        vector_db = get_vector_db()
        docs = vector_db.similarity_search_with_relevance_scores(query, k=5, filter=search_filter)
        
        valid_docs = [d for d, score in docs if score >= 0.65]
        if not valid_docs:
            client.chat_update(channel=channel_id, ts=status['ts'], text="🔍 관련 정보를 찾지 못했습니다.")
            return

        context = "\n".join([f"[{d.metadata['file_name']}] {d.page_content}" for d in valid_docs])
        prompt = ChatPromptTemplate.from_template("문서: {context}\n질문: {query}\n친절하게 답변해줘.")
        answer = (prompt | llm).invoke({"context": context, "query": query}).content

        client.chat_update(channel=channel_id, ts=status['ts'], text=f"🤖 {answer}")
        
        # 파일 전송 및 안내
        unique_files = list(set([d.metadata['file_name'] for d in valid_docs]))
        
        if unique_files:
            # 몇 개의 파일을 찾았는지 알려주는 메시지
            upload_info = client.chat_postMessage(channel=channel_id, text=f"📤 관련 문서 {len(unique_files)}건을 업로드합니다...")
            
            for file_name in unique_files:
                file_path = f"temp/{file_name}"
                if os.path.exists(file_path):
                    try:
                        # 파일 하나씩 업로드
                        client.files_upload_v2(
                            channel=channel_id, 
                            file=file_path, 
                            title=file_name,
                            initial_comment=f"📄 {file_name} (검색된 문서)"
                        )
                    except Exception as upload_err:
                        print(f"File upload failed: {file_name}, {upload_err}")

            # 업로드 안내 메시지는 깔끔하게 삭제
            client.chat_delete(channel=channel_id, ts=upload_info['ts'])
            
    except Exception as e:
        print(f"Search error: {e}")

@app.event("app_mention")
def handle_mention(event):
    # 봇이 보낸 메시지는 무시
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return
    
    # 1. 멘션된 봇 ID(<@U...>) 부분만 공백으로 치환
    query = re.sub(r"<@.*?>", "", event["text"]).strip()
    
    # 2. 질문 내용이 있다면 답변 실행 (채널 타입 상관없이)
    if query:
        smart_search_and_answer(query, event["channel"])
    else:
        # 질문 없이 봇만 불렀을 때의 매너 답변 (선택사항)
        client.chat_postMessage(channel=event["channel"], text="네! 궁금한 점을 말씀해 주세요.")

@app.event("message")
def handle_msg(event):
    if event.get("channel_type") == "im" and not event.get("bot_id"):
        smart_search_and_answer(event["text"], event["channel"])

if __name__ == "__main__":
    SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN")).start()
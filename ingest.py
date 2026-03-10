import os
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_postgres.vectorstores import PGVector
from langchain_core.documents import Document
from sqlalchemy import create_engine
from extract import get_text_from_file 

# DB 설정 (동일)
DB_PARAMS = {
    "driver": "psycopg",
    "host": os.getenv("DB_HOST", "host.docker.internal"),
    "database": "qna_bot",
    "user": "postgres",
    "password": "doc123",
    "port": 5432
}

CONNECTION_STRING = f"postgresql+psycopg://{DB_PARAMS['user']}:{DB_PARAMS['password']}@{DB_PARAMS['host']}:{DB_PARAMS['port']}/{DB_PARAMS['database']}"
COLLECTION_NAME = "slack_documents"

def get_vector_db():
    engine = create_engine(CONNECTION_STRING)
    return PGVector(
        connection=engine,
        collection_name=COLLECTION_NAME,
        embeddings=OpenAIEmbeddings(),
        use_jsonb=True
    )

def process_and_save_to_db(file_path, file_id, channel_id, is_locked, file_url, original_time):
    file_name = os.path.basename(file_path)
    
    if not os.path.exists(file_path):
        print(f"❌ [에러] 파일을 찾을 수 없음: {file_path}")
        return False, "파일 다운로드에 실패했습니다."

    try:
        # 1. 텍스트 추출 시도
        print(f"🔍 [진행] 텍스트 추출 중: {file_name}")
        raw_text = get_text_from_file(file_path)
        
        # 텍스트가 아예 없거나 공백만 있는 경우 체크
        if not raw_text or not raw_text.strip():
            print(f"❌ [에러] 추출된 텍스트 없음: {file_name}")
            return False, "텍스트를 추출할 수 없습니다. (이미지로만 구성되었거나 지원하지 않는 포맷)"

        # 2. Document 객체 생성 및 청킹
        doc = Document(page_content=raw_text, metadata={"source": file_name})
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        docs = text_splitter.split_documents([doc])
        
        target_id = channel_id if is_locked else "public"

        # 3. 메타데이터 주입
        for d in docs:
            d.metadata = {
                "file_id": str(file_id),
                "file_name": file_name,
                "channel_id": target_id,
                "is_locked": is_locked,
                "file_url": file_url,
                "uploaded_at": original_time
            }

        # 4. DB 저장
        print(f"💾 [진행] 벡터 DB 저장 중 ({len(docs)} chunks): {file_name}")
        vector_db = get_vector_db()
        # 중복 방지를 위해 기존 파일 ID 데이터 삭제
        vector_db.delete(filter={"file_id": str(file_id)})
        vector_db.add_documents(docs)
        
        print(f"✅ [성공] 등록 완료: {file_name}")
        return True, f"{len(docs)}개의 텍스트 조각으로 분할되어 저장되었습니다."

    except Exception as e:
        error_msg = str(e)
        print(f"❌ [에러] 처리 중 예외 발생: {error_msg}")
        return False, f"처리 중 오류 발생: {error_msg}"
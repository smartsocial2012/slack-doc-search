import os
import requests
from slack_sdk import WebClient
from dotenv import load_dotenv

load_dotenv()

client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
DOWNLOAD_DIR = "temp"

# 폴더가 없으면 생성
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def download_file(url, file_name):
    """슬랙 인증을 거쳐 파일을 다운로드합니다."""
    headers = {"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}
    response = requests.get(url, headers=headers, stream=True)
    
    if response.status_code == 200:
        path = os.path.join(DOWNLOAD_DIR, file_name)
        with open(path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                f.write(chunk)
        return path
    else:
        print(f"❌ 다운로드 실패: {response.status_code}")
        return None

def collect_and_download_docs():
    # 수집할 확장자들
    allowed_exts = ['pdf', 'pptx', 'docx', 'txt']
    print(f"📂 대상 확장자: {allowed_exts} 수집 및 다운로드 시작...")
    
    try:
        # 모든 파일 리스트업
        result = client.files_list(count=100) # 일단 최근 100개만
        files = result.get("files", [])
        
        for f in files:
            f_name = f["name"]
            f_id = f["id"]
            # 확장자 추출
            ext = f_name.split('.')[-1].lower() if '.' in f_name else ""
            
            if ext in allowed_exts:
                print(f"📥 다운로드 중: {f_name}...")
                download_url = f.get("url_private_download")
                
                if download_url:
                    saved_path = download_file(download_url, f_name)
                    if saved_path:
                        print(f"✅ 저장 완료: {saved_path}")
                else:
                    print(f"⚠️ 다운로드 URL이 없습니다: {f_name}")

    except Exception as e:
        print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    collect_and_download_docs()
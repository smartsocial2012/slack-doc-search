FROM python:3.11-slim

# 1. 작업 디렉토리 설정
WORKDIR /app

# 2. 시스템 필수 패키지 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 3. 가상환경 생성 및 경로 설정 (이게 핵심!)
# 시스템 pip와 충돌하지 않도록 독립된 공간을 만듭니다.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 4. pip 자체 업데이트 및 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir langchain==0.3.0 langchain-community==0.3.0 langchain-core==0.3.0

# 5. 소스 코드 복사
COPY . .

# 6. 실행
CMD ["python", "qnabot.py"]
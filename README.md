# 수령/반납 리포트 & 대량 문자 발송 도구

## 준비물
1. 안드로이드 폰에 [SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway) 설치
2. 앱에서 "Local Server" 켜기 → 화면에 뜨는 IP / 포트 / 아이디 / 비밀번호 확인
3. Python 3 설치 (PC: `winget install Python.Python.3.12` 또는 python.org / 폰: Termux)
4. [console.anthropic.com](https://console.anthropic.com)에서 Claude API 키 발급 (표 사진 읽기용)
5. (선택) 반납 항공편 실제 도착시간 조회를 쓰려면 [data.go.kr](https://www.data.go.kr/data/15095074/openapi.do)
   "인천국제공항공사_여객편 주간 운항 현황" 서비스키 발급

## 사용법
1. PC: `run.bat` 더블클릭 / 폰: Termux에서 `python server.py` 실행 → 브라우저에서 `localhost:8000` 접속
2. "4. 발송 설정"에 Claude API 키, (선택) 항공편 서비스키, SMS Gateway의 IP/포트/아이디/비밀번호 입력
   (전부 이 브라우저에만 저장되며 자동 저장됩니다. 같은 기기에서 쓸 땐 IP를 `127.0.0.1`로)
3. "연결 테스트"로 SMS Gateway 정상 연결 확인
4. "1. 표 사진 넣기"에서 대여/반납 표 캡처 이미지를 선택/붙여넣기/드래그 → 자동으로 표 추출
5. "2. 리포트 검수"에서 이름/번호/유형 확인, 틀린 값 수정, 필요 없는 사람은 "발송" 체크 해제
   (반납 건은 "반납 항공편 전체 조회"로 실제 도착시간 확인 가능)
6. "3. 문자 내용" 작성 → "5. 발송"

## 폰 하나로 쓰기 (Termux)
```bash
pkg install python git -y
git clone https://github.com/tpwnsdk1995-afk/autotext.git
cd autotext
python server.py
```
매번 실행하기 번거로우면 `termux-widget/start-tool.sh`를 `~/.shortcuts/`에 복사해서
Termux:Widget 홈 화면 위젯으로 등록하면 한 번 탭으로 서버 실행 + 브라우저까지 자동으로 열립니다.

## 주의사항
- `index.html`을 더블클릭해서 직접 열면(file://) 발송 기능이 CORS 오류로 동작하지 않습니다.
  반드시 서버(`run.bat` 또는 `python server.py`)로 실행하세요.
- Claude API 비용은 "사진을 분석시킬 때"만 발생합니다 (앱 실행 자체는 무료). 실제 사용량은
  console.anthropic.com의 Usage 메뉴에서 확인 가능합니다.
- 처음에는 본인 번호 2~3개로 소규모 테스트 후 실제 발송하는 것을 권장합니다.
- 한 번에 너무 많은 번호로 보내면 통신사에서 스팸으로 제재할 수 있어, 기본적으로 10건씩
  나눠 3초 간격으로 발송합니다 ("고급 설정"에서 조절 가능).
- "성공"은 폰이 요청을 받아들였다는 뜻이며, 실제 수신 확인까지는 포함하지 않습니다.
- 항공편 조회는 data.go.kr API가 제공하는 조회일 기준 D+0~D+6 범위만 가능합니다.

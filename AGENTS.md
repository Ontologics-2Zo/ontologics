# 저장소 개발 지침

개발용 Codex·개발자가 코드를 변경할 때 적용한다. 서비스 Agent 지시사항은 `docs/AGENT_RUNTIME.md`이며 이 파일은 운영 권한을 부여하지 않는다.

- 먼저 `PROJECT_STATUS.md`, `docs/SERVICE_SPEC.md`, 변경하는 도구의 `docs/TOOLS.md` 계약을 읽는다. 사용자 확정 O01~O08을 반복 질문하지 않는다.
- S1 조사·보완 및 S2 등록·검토·승인 전체 범위를 유지한다. BOM 생성은 원본 구조화이며 새 구성을 발명하지 않는다.
- 실제 제품·사양·BOM과 가상 운영 기록을 분리한다. 확인되지 않은 실장·호환성·고장·검사·가동 정보를 추가하지 않는다.
- 모델·식별·시간·권한은 공통 코드에서 관리한다. Agent별 복제, 직접 DB 쓰기, Agent의 승인 호출을 도입하지 않는다.
- 원본은 `data/sources/`에 무변경 보관하고 해시·원본 위치·라이선스를 유지한다. 정규화 결과만 바꾼다.
- 새로운 근거는 새 SourceDocument/Evidence ID를 쓴다. 사실 정정은 새 초안·승인·데이터 버전으로 남긴다.
- 관련 업무 결과 단위로 변경하고 기존 문서를 갱신한다. 근거·시간·권한 검증을 생략하지 않되 문서·도구·프레임워크를 불필요하게 늘리지 않는다.
- Python 3.10+, PostgreSQL + pgvector와 psycopg 3를 사용한다. SQLite fallback을 도입하지 않는다. 모델 키·DB 접속 비밀값·환경 파일·DB 덤프·실행 원문을 커밋하지 않는다.
- 변경 영향에 맞는 `python3 -m unittest tests.test_<영역> -v`와 최종 `python3 -m unittest discover -v`를 실행한다. 계약·시나리오 변경 시 `python3 -m ontologics.demo`와 호출 예제도 확인한다.
- `model.py`의 SCHEMA가 T-box 생성 기준이다. 계약을 바꾸면 스키마·규칙·문서·대표 데이터·검증과 버전을 함께 검토한다.
- `PROJECT_STATUS.md`에 완료 근거, 미검증, 다음 행동을 남긴다. 개발 시드·테스트 대역·실제 운영 성공을 구분한다.
- Skill은 반복이 확인된 개발 절차가 생길 때만 묶는다. 현재는 README의 공통 실행 절차를 재사용한다.

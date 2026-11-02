# 자재 관리 패키지 이름 변경

사용자 요청으로 기능 소유 이름을 production_planning에서 materials로 변경했다. 업무 계산과 승인·권한 계약은 유지한다.

- 패키지: ontologics.materials, 테스트: tests.materials, 실행 예제: examples.materials_*.
- 자료 디렉터리: data/materials. 기존 SourceDocument가 저장한 data/production_planning 경로는 상대 심볼릭 링크로 계속 해석한다. 모든 자료 파일의 바이트와 해시는 이동 전과 동일하다.
- 에이전트: materials 및 materials_review. 상수: MATERIALS_REGISTRATION, MATERIALS_PROMPT, MATERIALS_TOOLS.
- 클래스: MaterialsHost, MaterialsService, MaterialsApplication, MaterialsHandler, MaterialsServer. 제품 연결 검토 클래스는 LibraryProductReview이며 library_product_review.py에 둔다.
- 공통 model.py와 tbox.py는 새 패키지 import만 변경한다. 기존 DB 테이블·저장된 식별자·API 경로·생산계획 도메인 타입은 유지한다. 원본 생성 이력의 generator 문자열도 과거 사실로 보존한다.
- 새 실행: python -m ontologics.materials.contract_library_web --port 51171 --schema a_contract_library.
- 예전 Python import 경로는 제거했다. 외부 실행 스크립트도 새 모듈 이름을 사용해야 한다.

검증 결과는 PROJECT_STATUS.md의 이번 이름 변경 기록을 참고한다.

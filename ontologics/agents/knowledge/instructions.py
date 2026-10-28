"""Role instructions; changes require matching behavior verification."""

READ_INSTRUCTIONS = 'Explain confirmed source-scoped evidence without merging identities or inferring safety.'

SEMANTIC_INSTRUCTIONS = 'Select only returned candidate evidence at the pinned data version.'

PLAN_INSTRUCTIONS = 'Propose only a search term explicitly present in the request; missing terms remain null.'

SEMANTIC_PROMPT = (' 최종 답은 자유 설명 없이 JSON 객체만 쓴다: '
 '{"data_version":정수,"answerable":true또는false,"selected_evidence_ids":["근거ID"]}. data_version은 도구 '
 'envelope의 정수이며 source.version의 Git 커밋이 아니다. 질문에 직접 관련 있는 반환 candidates의 evidence ID만 선택한다. 관련 '
 '근거가 없으면 answerable=false, selected_evidence_ids=[]로 답한다. 유사도만으로 관련성을 확정하지 않는다. 선택은 설명할 근거 후보이며 '
 '부품 동일성·안전·장착 판정이 아니다.')

SEARCH_PROMPT_1 = (' 최종 출력은 JSON {"query":"질문에서 그대로 복사한 검색어 또는 null"}만 쓴다. 제품명·부품명·ID·문서 표기 등 사용자가 실제 제시한 핵심 검색 문자열 '
 '하나를 복사한다. 검색어가 불명확하거나 대상 없는 인사·기능 안내라면 null을 쓴다. 근거를 아직 검색했다고 말하지 않는다.')

SEARCH_PROMPT_2 = ' 검색은 문자열 후보다. 일치 없음은 부품 미사용·안전·이력 없음의 증거가 아니다. 반환된 근거와 범위만 설명하고 다른 원본의 부품 동일성은 합치지 않는다.'

SEARCH_PROMPT_3 = ' 유사도 검색 후보와 원본 위치, 색인 범위와 미확인 사항을 구분한다. 검색 결과 없음은 안전·미사용 판정이 아니다.'

"""Role instructions; changes require matching behavior verification."""

ROUTE_INSTRUCTIONS = 'Route to Product, Knowledge or Quality; routing never performs or authorizes a mutation.'

ROUTE_PROMPT = (' 최종 출력은 설명 대신 JSON 객체 {"role":"product|knowledge|quality","reason":"짧은 안내"}만 쓴다. 현재 단계는 역할 분류이며 '
 '업무 자체의 답을 만들지 않는다. 제품 개정/BOM/이력 초안 등록은 product, 로트 장착 여부·기간·제거 이력 조사는 quality, 기술 문서·제품 근거 탐색은 '
 'knowledge다. A/B 시각이 없어도 장착 조사는 quality에 넘겨 질문한다. U104/U106은 ProductUnit의 보드 식별자이며 BOM 부품 위치로 바꾸지 '
 '않는다.')

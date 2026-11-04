'use strict';
// A owns interpretation/material arithmetic. This screen supplies explicit human input only.
async function mountPlanning(container) {
  const names = {contract_item_id:'계약 품목 번호',company:'회사',customer:'고객',manufacturer:'제조사',product_model:'제품 모델',revision:'개정',variant:'변형',configuration_id:'구성 ID',ordered_quantity:'주문 수량',unit:'원문 단위',fulfilled_quantity:'기납품 수량',allocated_quantity:'기배정 수량',planned_input_quantity:'생산 검토 수량',delivery_due:'납기 원문',constraints:'개정·대체 제한'};
  const call = async (action, body) => must(await api('/api/planning/' + action, body)).data;
  const save = (action, body) => call(action, {...body, request_key:requestKey('planning:' + action, body)});
  const can = permission => context?.identity?.permissions?.includes(permission);
  const button = (label, action, permission) => {
    const b = actionButton(label, action);
    if (permission && !can(permission)) b.disabled = true;
    return b;
  };
  const field = (title, type='text', value='') => {
    const label=node('label',title), input=node(type==='textarea'?'textarea':'input');
    if (type!=='textarea') input.type=type;
    input.value=value; label.append(input); return {label,input};
  };
  const frame=node('section',null,'card');
  frame.append(node('h2','계약 원본에서 자재 소요량 검토'),node('p','원본 접수 → AI 해석 → 사람이 원문 대조 → 제품 구성 연결 → 계산·검토 저장. 해석 확인과 계산 저장은 그래프 승인이나 생산·구매 실행이 아닙니다.','hint'));
  const searchBar=node('div',null,'actions'), query=field('계약 검색'), list=node('div'), detail=node('section');
  searchBar.append(query.label,button('계약 검색',()=>search()));
  const receipt=node('details'); receipt.append(node('summary','새 계약 원본 접수'));
  const form=node('form'), title=field('계약 제목'), customer=field('고객·상대방 (선택)'), file=field('원본 UTF-8 TXT','file');
  title.input.required=true;title.input.maxLength=200;customer.input.maxLength=200;file.input.required=true;file.input.accept='.txt';
  const upload=node('button','원본 자료 접수','primary');upload.type='submit';upload.disabled=!can('draft');
  form.append(title.label,customer.label,file.label,node('p','600 KB 이하 UTF-8 TXT. PDF는 A 계약용 격리 파서 연결 전까지 이 화면에서 해석하지 않습니다. 접수만으로 모델을 호출하지 않습니다. 시연 자료는 가상 계약이라고 원문에 표시하세요.','hint'),upload);
  form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;run(async()=>{
    const selected=file.input.files[0];if(selected.size>600000)throw Error('600 KB 이하 파일을 선택하세요.');
    const bytes=new Uint8Array(await selected.arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
    const result=await save('register',{title:title.input.value,counterparty:customer.input.value,filename:selected.name,content_base64:btoa(binary)});
    await search();await open(result.id);message('원본을 접수했습니다. AI 해석은 별도 버튼으로 요청하세요.');
  },upload);});
  receipt.append(form);frame.append(searchBar,list,receipt,detail);container.replaceChildren(frame);

  async function search() {
    const data=await call('search',{query:query.input.value,offset:0});list.replaceChildren();
    list.append(node('p',`검색 결과 ${data.total}건 · 최근 ${data.items.length}건 표시`,'hint'));
    if(data.items.length)list.append(simpleTable(['계약','상대방','접수','열기'],data.items.map(row=>[row.title,row.counterparty||'미기록',row.received_at,button('계약 열기',()=>open(row.id))])));
    else list.append(node('p','접수된 계약이 없습니다. 기존 A 자료는 같은 서버 DB/schema를 사용할 때 표시됩니다.','empty-row'));
  }

  async function open(id) {
    detail.replaceChildren(node('p','계약을 읽는 중…','hint'));
    const data=await call('get',{contract_id:id});detail.replaceChildren();
    detail.append(node('h2',data.title),node('p',`${data.filename} · 원본 버전 1 · SHA-256 ${data.sha256}`,'hint'));
    const original=node('details');original.append(node('summary','접수 원본 확인'));
    if(data.text!==null)original.append(node('pre',data.text));
    original.append(button('원본 파일 다운로드',async()=>{
      const bytes=Uint8Array.from(atob(data.content_base64),c=>c.charCodeAt(0));
      const url=URL.createObjectURL(new Blob([bytes],{type:data.mime}));const link=node('a','다운로드');link.href=url;link.download=data.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }));detail.append(original);
    const interpret=button('AI 계약 해석 요청',async()=>{
      const result=await save('interpret',{contract_id:id});await open(id);
      if(result?.status!=='needs_review')throw Error('계약 해석을 완료하지 못했습니다. 아래 오류와 원본을 확인하세요.');
    },'draft');
    interpret.disabled=interpret.disabled||!context?.model?.configured||data.mime!=='text/plain';
    if(data.mime!=='text/plain')detail.append(node('p','기존 PDF 원본은 보존되어 있습니다. 공통 화면의 A 계약 PDF 해석은 격리 파서 연결 후 지원합니다.','message'));
    detail.append(interpret,node('p','모델 호출 비용이 발생합니다. 원본에 없는 조건은 미확인으로 남기고, 결과를 사람이 대조한 뒤 제품을 연결합니다.','hint'));
    const review=data.review;
    if(!review){detail.append(node('p','아직 해석 결과가 없습니다.','hint'));return;}
    if(review.status!=='needs_review'){
      detail.append(node('p',`해석 상태: ${review.status} · 확인 필요`,'message error'),jsonDetails('해석 오류',review.result?.errors||[]));return;
    }
    renderReview(data,review);await showPlanning(id);
  }

  function renderReview(data, review) {
    const section=node('section');section.append(node('h3','원문 대조·수정'),node('p','근거를 확인하고 값을 유지·수정하거나 미확인으로 남기세요. 미확인 값을 임의로 확정하지 않습니다.','hint'));
    const previous=[...(review.checks||[])].reverse().find(check=>check.decisions), controls=[];
    review.result.items.forEach((item,index)=>{
      section.append(node('h4',`계약 품목 ${index+1}`));
      const rows=Object.entries(item.fields).map(([name,f])=>{
        const key=`${index}:${name}`, old=previous?.decisions?.[key];
        const choice=node('select');choice.setAttribute('aria-label',`품목 ${index+1} ${names[name]||name} 검토 상태`);
        for(const [value,label]of [['unresolved','미확인'],['accepted','원문 해석 유지'],['corrected','원문 확인 후 수정']]){
          const option=node('option',label);option.value=value;option.disabled=value==='accepted'&&f.state!=='supported';choice.append(option);
        }
        choice.value=old?.state||'unresolved';
        const input=node('input');input.value=old?.value??f.value??'';input.maxLength=2000;input.setAttribute('aria-label',`품목 ${index+1} ${names[name]||name} 수정 값`);
        const reason=node('input');reason.value=old?.reason||'';reason.maxLength=2000;reason.placeholder='수정 근거·사유';reason.setAttribute('aria-label',`품목 ${index+1} ${names[name]||name} 수정 근거`);
        const sync=()=>{input.disabled=choice.value!=='corrected';reason.disabled=choice.value!=='corrected';};choice.addEventListener('change',sync);sync();
        const evidence=node('details');evidence.append(node('summary','원문 근거'));
        for(const claim of f.claims||[])for(const ref of claim.evidence||[])evidence.append(node('blockquote',`${ref.quote} · ${ref.segment_id}`));
        controls.push({key,f,choice,input,reason});
        return [names[name]||name,f.value==null?'미확인':String(f.value),evidence,choice,input,reason];
      });section.append(simpleTable(['항목','해석 값','근거','검토','수정 값','사유'],rows));
    });
    const note=field('검토 의견 (선택)','textarea',previous?.note||'');note.input.maxLength=2000;section.append(note.label);
    section.append(button('원문 대조 결과 저장',async()=>{
      const decisions={};for(const c of controls){
        const state=c.choice.value;
        if(state==='corrected'&&(!c.input.value.trim()||!c.reason.value.trim()))throw Error('수정한 항목은 수정 값과 근거를 모두 입력하세요.');
        decisions[c.key]={state,value:state==='accepted'?c.f.value:state==='corrected'?c.input.value:null,reason:state==='corrected'?c.reason.value:''};
      }
      await save('review',{contract_id:data.id,interpretation_id:review.id,result_sha256:review.result_sha256,decisions,note:note.input.value});await open(data.id);
      message('원문 대조를 저장했습니다. 미확인은 유지되며 생산 승인·확정 등록이 아닙니다.');
    },'review'));
    if(review.checks?.length)section.append(jsonDetails('이전 원문 대조 기록',review.checks));
    detail.append(section);
  }

  async function showPlanning(id) {
    const data=await call('planning',{contract_id:id}), section=node('section');
    section.append(node('h3','제품 구성 연결'),node('p',`제품 데이터 v${data.data_version??'미확인'} · 확인한 계약 조건과 일치하는 구성만 연결합니다.`,'hint'));
    if(data.status==='review_required')section.append(node('p','원문 대조 결과를 먼저 저장하세요.','hint'));
    for(const [index,item]of data.items.entries()){
      const panel=node('div',null,'card');panel.append(node('h4',`품목 ${index+1}`));
      const rows=(item.candidates||[]).map(candidate=>{
        const details=node('details');details.append(node('summary','일치 조건·근거'),simpleTable(['조건','계약','제품 구성'],Object.entries(candidate.comparison).map(([k,v])=>[names[k]||k,v.contract??'미확인',v.catalog])));
        for(const source of candidate.sources||[])details.append(node('p',`${source.source} · ${source.locator} · ${source.description}`));
        const connected=item.link_current&&item.link.configuration_id===candidate.configuration_id;
        const connect=button(connected?'연결됨':'이 구성 연결',async()=>{
          await save('connect',{contract_id:id,interpretation_id:data.interpretation_id,review_id:data.review_id,data_version:data.data_version,item_index:index,configuration_id:candidate.configuration_id});await open(id);
        },'review');connect.disabled=connect.disabled||!candidate.exact||connected;
        return [candidate.name,candidate.exact?'조건 일치':'불일치·확인 필요',details,connect];
      });
      if(rows.length)panel.append(simpleTable(['구성','판정','근거','연결'],rows));
      if(item.link&&!item.link_current)panel.append(node('p','계약 대조 또는 제품 데이터가 바뀌었습니다. 이전 연결을 다시 확인하세요.','message error'));
      if(item.link_current)panel.append(button('부품 소요량·재고 검토',()=>showMaterials(id,index)));
      section.append(panel);
    }
    detail.append(section);
  }

  async function showMaterials(id,index) {
    const data=await call('materials',{contract_id:id,item_index:index});detail.replaceChildren();
    detail.append(button('계약·제품 연결로 돌아가기',()=>open(id)),node('h2','부품 소요량·재고 검토'),node('p',data.title));
    detail.append(node('p','주문 수량을 생산 수량으로 자동 간주하지 않습니다. 이번 검토 수량과 근거를 입력하세요. 계산 저장은 생산 승인·자재 예약·구매가 아닙니다.','hint'));
    const form=node('form'),quantity=field('생산 검토 수량 (대)','number',data.saved_current?data.saved.quantity:''),reason=field('수량 산정 근거','textarea',data.saved_current?data.saved.reason:'');
    quantity.input.required=true;quantity.input.min=0;quantity.input.max=10000000;quantity.input.step=1;reason.input.required=true;reason.input.maxLength=2000;
    const submit=node('button','소요량 계산·검토 저장','primary');submit.type='submit';submit.disabled=!can('review');form.append(quantity.label,reason.label,submit);
    form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;run(async()=>{
      await save('materials-calculate',{contract_id:id,item_index:index,link_id:data.link.id,quantity:Number(quantity.input.value),reason:reason.input.value});await showMaterials(id,index);
    },submit);});detail.append(form);
    if(data.stock)detail.append(node('p',`${data.stock.label} · ${data.stock.origin==='synthetic'?'가상 운영 기록':'자료 구분 확인 필요'} · ${data.stock.scope.location} · ${data.stock.scope.as_of}`,'hint'));
    else detail.append(node('p','재고 자료 미연결: 가용 수량과 부족량을 0으로 간주하지 않습니다.','message'));
    if(data.stock_changed)detail.append(node('p','재고 또는 예약이 변경됐습니다. 아래 이전 결과를 확정하지 말고 다시 계산하세요.','message error'));
    if(data.saved_current){
      const result=data.saved.result;
      detail.append(node('p',`${data.saved.quantity}대 · 데이터 v${result.data_version} · ${data.saved.actor} · ${data.saved.created_at}`,'hint'));
      detail.append(simpleTable(['부품','1대당','필요량','가용량','부족량'],result.components.map(row=>[row.name,...['q','D','A','S'].map(k=>row[k]===null?'미확인':row[k])])));
      detail.append(node('p','부품 단위: 개. 실장 여부·재고 범위 미확인은 유지합니다.','hint'),jsonDetails('계산 근거·추가 확인 사항',result));
    }
  }
  await search();
}

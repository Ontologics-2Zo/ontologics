'use strict';
// A owns interpretation/material arithmetic. This screen supplies explicit human input only.
async function mountPlanning(container) {
  const names = {contract_item_id:'계약 품목 번호',company:'회사',customer:'고객',manufacturer:'제조사',product_model:'제품 모델',revision:'개정',variant:'변형',configuration_id:'구성 ID',ordered_quantity:'주문 수량',unit:'원문 단위',fulfilled_quantity:'기납품 수량',allocated_quantity:'기배정 수량',planned_input_quantity:'생산 검토 수량',delivery_due:'납기 원문',constraints:'개정·대체 제한'};
  const call = async (action, body) => must(await api('/api/planning/' + action, body)).data;
  const save = (action, body) => call(action, {...body, request_key:requestKey('planning:' + action, body)});
  // Deliberate review/recalculation gets a new key; transport retries keep this body's key.
  const reviewSave = (action, body) => call(action, {...body, request_key:crypto.randomUUID()});
  const can = permission => context?.identity?.permissions?.includes(permission);
  const button = (label, action, permission) => {
    const b = actionButton(label, () => attempt(action));
    if (permission && !can(permission)) b.disabled = true;
    return b;
  };
  const field = (title, type='text', value='') => {
    const label=node('label',title), input=node(type==='textarea'?'textarea':'input');
    if (type!=='textarea') input.type=type;
    input.value=value; label.append(input); return {label,input};
  };
  const shell=node('section',null,'materials-workspace'), tabs=node('div',null,'mat-tabs');
  const feedback=node('div');feedback.hidden=true;feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');
  // A-only stylesheet constructed by this trusted script; the host disallows inline style tags.
  if(!mountPlanning.styleSheet){
    const style=new CSSStyleSheet();style.replaceSync(`
    .materials-workspace{--mat-accent:#006d77;overflow-wrap:anywhere;scroll-margin-top:85px}
    .materials-workspace button:disabled{cursor:not-allowed}
    .materials-workspace[aria-busy=true] button{opacity:.55;cursor:wait}
    .materials-workspace [hidden]{display:none!important}
    .materials-workspace .mat-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
    .materials-workspace .mat-tabs button{padding:11px 18px;font-size:13px}
    .materials-workspace .mat-page{padding:22px;border:1px solid #d9e3e7;border-radius:8px;background:white}
    .materials-workspace .mat-page h2{font-size:21px;margin:5px 0 8px}
    .materials-workspace .mat-steps{display:flex;list-style:none;padding:0;margin:0 0 20px;gap:8px;flex-wrap:wrap}
    .materials-workspace .mat-steps li{display:flex;gap:7px;align-items:center;font-size:12px;padding:8px 12px;background:#f2f5f7;color:#5a6e7a;border-radius:6px}
    .materials-workspace .mat-steps li[aria-current=step]{background:#006d77;color:white;font-weight:700}
    .materials-workspace .mat-step-no{font-weight:800}
    .materials-workspace .mat-next{background:#eff7f8;border-left:3px solid #168794;padding:11px 14px;margin:15px 0;color:#335866;font-size:13px}
    .materials-workspace .mat-notice{padding:13px 16px;border-radius:6px;background:#e8f5ee;color:#1b634b;margin-bottom:16px;font-size:13px}
    .materials-workspace .mat-notice.error{background:#fff0f1;color:#a02d36}
    .materials-workspace .mat-notice.pending{background:#edf4f8;color:#31586d}
    .materials-workspace .mat-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;max-width:760px}
    .materials-workspace .mat-grid .wide{grid-column:1/-1}
    .materials-workspace .mat-primary{font-size:14px;padding:12px 20px;background:#006d77;color:white;border-color:#006d77}
    .materials-workspace .mat-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:22px 0 10px}
    .materials-workspace .mat-actions button{width:auto}
    .materials-workspace .mat-context{padding:12px 15px;background:#f5f8fa;border:1px solid #e2e9ed;border-radius:6px;margin:12px 0}
    .materials-workspace .mat-context p{margin:3px 0;font-size:12px}
    .materials-workspace .mat-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}
    .materials-workspace .mat-stat{border:1px solid #dce5e9;border-radius:7px;padding:15px;background:#f8fbfc}
    .materials-workspace .mat-stat strong{display:block;font-size:27px;color:#124b5b;margin-top:6px}
    .materials-workspace .mat-stat span{font-size:12px;color:#59707e}
    .materials-workspace .mat-choice{border:1px solid #d6e1e6;border-radius:8px;padding:18px;margin:14px 0}
    .materials-workspace .mat-choice h3{font-size:17px;margin:0 0 8px}
    .materials-workspace .mat-choice-head{display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}
    .materials-workspace .mat-choice-head p{margin:5px 0}
    .materials-workspace summary{font-size:13px;cursor:pointer}
    .materials-workspace details{margin:12px 0}
    .materials-workspace .mat-back{margin-bottom:14px}
    .materials-workspace .mat-page:focus{outline:none}
    .materials-workspace .mat-rfq{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fb;border:1px solid #d9e3e7;padding:18px;max-height:55vh;overflow:auto;font-size:12px}
    @media(max-width:760px){.materials-workspace .mat-grid,.materials-workspace .mat-stats{grid-template-columns:1fr}.materials-workspace .mat-page{padding:14px}.materials-workspace .mat-tabs button{flex:1;padding:9px}.materials-workspace .mat-steps li{padding:6px;font-size:11px}}
    `);mountPlanning.styleSheet=style;
  }
  if(!document.adoptedStyleSheets.includes(mountPlanning.styleSheet))document.adoptedStyleSheets=[...document.adoptedStyleSheets,mountPlanning.styleSheet];
  const frame=node('section'),requests=node('section'),products=node('section'),handoffHistory=node('section');
  const panels=[frame,requests,products,handoffHistory],tabButtons=[];let loadRequests,loadHandoffHistory,reviewDirty=false;
  function focusPage(target){requestAnimationFrame(()=>{if(!target.isConnected||target.closest('[hidden]'))return;target.tabIndex=-1;target.focus({preventScroll:true});shell.scrollIntoView({block:'start',behavior:'instant'});});}
  function notice(text,type=''){feedback.textContent=text;feedback.className='mat-notice '+type;feedback.hidden=!text;}
  let pending=0;
  function syncBusy(){
    const busy=pending>0||isBusy||developmentSwitchPending;
    shell.inert=busy;if(busy)shell.setAttribute('aria-busy','true');else shell.removeAttribute('aria-busy');
  }
  async function attempt(action){
    pending++;syncBusy();
    try{return await action();}
    catch(error){notice(error.message||'처리하지 못했습니다. 입력 내용을 유지했으니 확인 후 다시 시도하세요.','error');focusPage(feedback);throw error;}
    finally{pending--;syncBusy();}
  }
  const primary=(label,action,permission)=>{const b=button(label,action,permission);b.classList.add('mat-primary');return b;};
  const actions=(...children)=>{const area=node('div',null,'mat-actions');area.append(...children);return area;};
  function flow(target,labels){
    const steps=node('ol',null,'mat-steps'),pages=labels.map(()=>node('section',null,'mat-page'));
    steps.setAttribute('aria-label','진행 단계');
    labels.forEach((label,i)=>{const li=node('li');li.append(node('span',String(i+1),'mat-step-no'),node('span',label));steps.append(li);pages[i].hidden=i!==0;});
    target.append(steps,...pages);
    const show=(index,text='',move=true)=>{pages.forEach((page,i)=>{page.hidden=i!==index;const li=steps.children[i];if(i===index)li.setAttribute('aria-current','step');else li.removeAttribute('aria-current');});notice(text);if(move)focusPage(pages[index]);};
    show(0,'',false);return {pages,show};
  }
  function heading(target,title,guide){target.append(node('h2',title),node('p',guide,'mat-next'));}
  function contextBox(...texts){const box=node('div',null,'mat-context');texts.filter(Boolean).forEach(text=>box.append(node('p',text)));return box;}
  tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','자재 검토 업무');
  panels.forEach((panel,index)=>{
    panel.id='materials-panel-'+index;panel.setAttribute('role','tabpanel');panel.hidden=index!==0;
    const b=button(['계약 검토','추가 생산 요청','제품별 자재','구매팀 전달 기록'][index],async()=>{
      panels.forEach((p,i)=>{p.hidden=i!==index;tabButtons[i].setAttribute('aria-selected',String(i===index));tabButtons[i].classList.toggle('primary',i===index);});
      notice('');if(index===1&&loadRequests)await loadRequests();if(index===3&&loadHandoffHistory)await loadHandoffHistory();focusPage(panel);
    });
    b.id='materials-tab-'+index;b.setAttribute('role','tab');b.setAttribute('aria-controls',panel.id);
    b.setAttribute('aria-selected',String(index===0));b.classList.toggle('primary',index===0);panel.setAttribute('aria-labelledby',b.id);
    tabButtons.push(b);tabs.append(b);
  });
  shell.append(tabs,feedback,...panels);container.replaceChildren(shell);
  // The common host may still be loading its records after this mount finishes.
  // Follow its existing busy indicator so newly rendered A controls cannot be clicked too early.
  mountPlanning.busyObserver?.disconnect();
  const busyObserver=new MutationObserver(()=>{if(!shell.isConnected){busyObserver.disconnect();return;}syncBusy();});
  busyObserver.observe($('dev-user-select'),{attributes:true,attributeFilter:['disabled']});
  mountPlanning.busyObserver=busyObserver;syncBusy();
  const contractFlow=flow(frame,['계약 선택','원문 대조','제품 연결','계산 조건','검토 결과']);
  const [contractList,detail,connection,conditions,contractResult]=contractFlow.pages;
  heading(contractList,'검토할 계약을 선택하세요','기존 계약을 열거나 새 원본을 접수하세요. 원문 확인부터 자재 계산까지 순서대로 진행합니다.');
  const searchBar=node('div',null,'actions'),query=field('계약 검색'),list=node('div');
  searchBar.append(query.label,button('계약 검색',()=>search()));
  query.input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();run(()=>attempt(search));}});
  const receipt=node('details');receipt.append(node('summary','＋ 새 계약 원본 접수'));
  const form=node('form'),title=field('계약 제목'),customer=field('고객·상대방 (선택)'),file=field('원본 UTF-8 TXT','file');
  title.input.required=true;title.input.maxLength=200;customer.input.maxLength=200;file.input.required=true;file.input.accept='.txt';
  const upload=node('button','원본 접수하고 다음 단계로','mat-primary');upload.type='submit';upload.disabled=!can('draft');
  form.append(title.label,customer.label,file.label,node('p','600 KB 이하 UTF-8 TXT. 접수 후 AI 해석을 요청할 수 있습니다.','hint'),upload);
  form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;run(()=>attempt(async()=>{
    const selected=file.input.files[0];if(selected.size>600000)throw Error('600 KB 이하 파일을 선택하세요.');
    const bytes=new Uint8Array(await selected.arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
    const result=await save('register',{title:title.input.value,counterparty:customer.input.value,filename:selected.name,content_base64:btoa(binary)});
    await search();await open(result.id,1,'원본을 접수했습니다. 이제 AI 계약 해석을 요청하세요.');
  }),upload);});
  receipt.append(form);contractList.append(searchBar,list,receipt);

  async function search() {
    const data=await call('search',{query:query.input.value,offset:0});list.hidden=false;list.replaceChildren();
    list.append(node('p',`검색 결과 ${data.total}건 · 최근 ${data.items.length}건 표시`,'hint'));
    if(data.items.length)list.append(simpleTable(['계약','상대방','접수','열기'],data.items.map(row=>[row.title,row.counterparty||'미기록',row.received_at,button('계약 열기',()=>open(row.id))])));
    else list.append(node('p','접수된 계약이 없습니다. 기존 A 자료는 같은 서버 DB/schema를 사용할 때 표시됩니다.','empty-row'));
  }

  async function open(id,stage=null,completed='') {
    notice('계약과 저장된 검토 상태를 불러오는 중입니다.','pending');
    const data=await call('get',{contract_id:id});
    reviewDirty=false;
    detail.replaceChildren(button('← 계약 목록',()=>contractFlow.show(0)));
    heading(detail,'계약 원문과 해석을 확인하세요','원문과 해석 값을 대조해 저장하면 제품 연결 단계로 이동합니다.');
    detail.append(contextBox(data.title,`${data.filename} · 원본 버전 1`));
    const original=node('details');original.append(node('summary','계약 원문 보기'));
    if(data.text!==null)original.append(node('pre',data.text));
    original.append(node('p','SHA-256 '+data.sha256,'hint'),button('원본 파일 다운로드',async()=>{
      const bytes=Uint8Array.from(atob(data.content_base64),c=>c.charCodeAt(0));
      const url=URL.createObjectURL(new Blob([bytes],{type:data.mime}));const link=node('a');link.href=url;link.download=data.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }));detail.append(original);
    const review=data.review;
    const interpret=button(review?'AI 해석 다시 요청':'AI 계약 해석 요청',async()=>{
      notice('AI가 계약을 해석하고 있습니다. 완료되면 원문 대조 항목을 표시합니다.','pending');
      const result=await save('interpret',{contract_id:id});await open(id,1,'AI 해석이 완료됐습니다. 아래 항목을 원문과 대조한 뒤 저장하세요.');
      if(result?.status!=='needs_review')throw Error('계약 해석을 완료하지 못했습니다. 해석 오류와 원본을 확인하세요.');
    },'draft');
    if(!review)interpret.classList.add('mat-primary');
    interpret.disabled=interpret.disabled||!context?.model?.configured||data.mime!=='text/plain';
    if(data.mime!=='text/plain')detail.append(node('p','기존 PDF 원본은 보존되어 있습니다. 이 화면의 AI 해석은 UTF-8 TXT만 지원합니다.','message'));
    if(!context?.model?.configured)detail.append(node('p','AI 모델 연결 설정이 필요합니다. 저장된 결과가 있으면 계속 검토할 수 있습니다.','message'));
    detail.append(interpret,node('p','AI 해석은 모델 호출 비용이 발생합니다. 원문에 없는 조건은 미확인으로 유지하세요.','hint'));
    if(review?.status==='needs_review'){
      renderReview(data,review);await showPlanning(id,data.title,review);
      if(review.checks?.length)detail.prepend(actions(primary('저장된 대조로 제품 연결 보기 →',()=>contractFlow.show(2))));
    }else if(review)detail.append(node('p',`해석 상태: ${review.status} · 원본 확인 후 다시 요청하세요.`,'message error'),jsonDetails('해석 오류',review.result?.errors||[]));
    else detail.append(node('p','아직 해석 결과가 없습니다. 위의 AI 계약 해석 요청부터 시작하세요.','hint'));
    if(review?.status!=='needs_review')connection.replaceChildren();
    contractFlow.show(stage===2&&review?.status!=='needs_review'?1:stage??(review?.status==='needs_review'&&review.checks?.length?2:1),completed);
  }

  function renderReview(data, review) {
    const section=node('section');section.append(node('h3','원문 대조·수정'),node('p','근거를 확인하고 값을 유지·수정하거나 미확인으로 남기세요. 미확인 값을 임의로 확정하지 않습니다.','hint'),node('p','제품 연결에는 제조사·제품 모델·개정·변형의 확인이 필요합니다. 원문과 해석 값이 맞으면 해당 항목의 검토를 ‘원문 해석 유지’로 선택하세요. 저장 버튼만 누르면 미확인 상태가 그대로 저장됩니다.','mat-next'));
    section.addEventListener('input',()=>{reviewDirty=true;});section.addEventListener('change',()=>{reviewDirty=true;});
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
        // Keep the complete review payload; the internal ID is shown at product linking, not source review.
        if(name==='configuration_id')return null;
        return [names[name]||name,f.value==null?'미확인':String(f.value),evidence,choice,input,reason];
      }).filter(Boolean);section.append(simpleTable(['항목','해석 값','근거','검토','수정 값','사유'],rows));
    });
    const note=field('검토 의견 (선택)','textarea',previous?.note||'');note.input.maxLength=2000;section.append(note.label);
    section.append(primary('원문 대조 저장하고 제품 연결 →',async()=>{
      const decisions={};for(const c of controls){
        const state=c.choice.value;
        if(state==='corrected'&&(!c.input.value.trim()||!c.reason.value.trim()))throw Error('수정한 항목은 수정 값과 근거를 모두 입력하세요.');
        decisions[c.key]={state,value:state==='accepted'?c.f.value:state==='corrected'?c.input.value:null,reason:state==='corrected'?c.reason.value:''};
      }
      await reviewSave('review',{contract_id:data.id,interpretation_id:review.id,result_sha256:review.result_sha256,decisions,note:note.input.value});await open(data.id,2,'원문 대조를 저장했습니다. 계약 조건과 일치하는 제품 구성을 선택하세요.');
    },'review'));
    if(review.checks?.length)section.append(jsonDetails('이전 원문 대조 기록',review.checks));
    detail.append(section);
  }

  function requireSavedReview() {
    if(reviewDirty){contractFlow.show(1);throw Error('원문 대조에 저장하지 않은 수정이 있습니다. 먼저 해당 화면에서 저장하세요.');}
  }

  async function showPlanning(id,title,review) {
    const data=await call('planning',{contract_id:id});connection.replaceChildren(button('← 원문 대조',()=>contractFlow.show(1)));
    const previous=[...(review?.checks||[])].reverse().find(check=>check.decisions);
    const currentBasis=review?.id===data.interpretation_id&&(previous?.id||null)===(data.review_id||null);
    heading(connection,'생산할 제품 구성을 연결하세요','계약 조건과 일치하는 구성을 선택하세요. 연결하면 수량과 자재 계산으로 진행합니다.');
    connection.append(contextBox(title,`제품 데이터 v${data.data_version??'미확인'}`));
    if(data.status==='review_required')connection.append(node('p','원문 대조 결과를 먼저 저장하세요.','message'));
    for(const [index,item]of data.items.entries()){
      const panel=node('div',null,'mat-choice');panel.append(node('h3',`계약 품목 ${index+1}`));
      const candidates=item.candidates||[],eligible=candidates.filter(candidate=>candidate.exact),others=candidates.filter(candidate=>!candidate.exact);
      const missing=item.missing||['manufacturer','product_model','revision','variant'].filter(key=>!item.fields?.[key]?.value);
      if(missing.length){
        const fields=review?.result?.items?.[index]?.fields||{};
        const supported=missing.filter(key=>item.fields?.[key]?.state==='unresolved'&&fields[key]?.state==='supported'&&fields[key].value!=null&&fields[key].claims?.some(claim=>claim.evidence?.some(ref=>ref.quote)));
        panel.append(node('p','아래 제품 정보가 아직 미확인 상태입니다. 값과 근거가 맞으면 이 화면에서 확인·저장할 수 있습니다.','message'));
        panel.append(simpleTable(['확인이 필요한 항목','AI 해석 값 · 확인 전','계약 원문 근거'],missing.map(key=>{
          const evidence=node('div');
          for(const claim of fields[key]?.claims||[])for(const ref of claim.evidence||[])evidence.append(node('blockquote',`${ref.quote} · ${ref.segment_id}`));
          if(!supported.includes(key))evidence.append(node('p','원문 대조에서 값과 수정 사유를 입력해야 합니다.','hint'));
          if(item.fields?.[key]?.reason)evidence.append(node('p',`기존 검토 사유: ${item.fields[key].reason}`));
          return [names[key]||key,fields[key]?.value??'원문에서 확인 필요',evidence];
        })));
        const next=actions();
        if(supported.length&&currentBasis){
          panel.append(node('p',`${supported.map(key=>names[key]||key).join(' · ')} 값을 원문과 대조했다면 확인·저장을 누르세요. 이 항목만 확인 처리합니다.`,'hint'));
          next.append(primary(supported.length===missing.length?'위 제품 정보 확인·저장 →':`근거 있는 ${supported.length}개 제품 정보 확인·저장 →`,async()=>{
            requireSavedReview();
            const fresh=await call('get',{contract_id:id}),latest=fresh.review;
            const latestCheck=[...(latest?.checks||[])].reverse().find(check=>check.decisions);
            if(latest?.id!==review.id||latest?.result_sha256!==review.result_sha256||(latestCheck?.id||null)!==(previous?.id||null)){
              await open(id,2);throw Error('저장된 원문 대조가 바뀌었습니다. 최신 값과 근거를 확인한 뒤 다시 눌러주세요.');
            }
            const decisions={};
            for(const [i,entry]of latest.result.items.entries())for(const key of Object.keys(entry.fields)){
              const scope=`${i}:${key}`;
              decisions[scope]=structuredClone(latestCheck?.decisions?.[scope]||{state:'unresolved',value:null,reason:''});
            }
            for(const key of supported)decisions[`${index}:${key}`]={state:'accepted',value:fields[key].value,reason:''};
            await reviewSave('review',{contract_id:id,interpretation_id:review.id,result_sha256:review.result_sha256,decisions,note:latestCheck?.note||''});
            await open(id,2,'제품 정보 확인을 저장했습니다. 아래의 일치하는 구성을 연결하세요.');
          },'review'));
          if(!can('review'))panel.append(node('p','제품 정보를 확인·저장하려면 검토 권한이 필요합니다.','message'));
        }else if(!currentBasis)next.append(button('최신 검토 다시 불러오기',()=>{requireSavedReview();return open(id,2);}));
        next.append(button('값이 다르거나 미확인인 항목 수정',()=>contractFlow.show(1)));panel.append(next);
      }
      const rows=eligible.map(candidate=>{
        const details=node('details');details.append(node('summary','일치 조건·근거'),simpleTable(['조건','계약','제품 구성'],Object.entries(candidate.comparison).map(([k,v])=>[names[k]||k,v.contract??'미확인',v.catalog])));
        for(const source of candidate.sources||[])details.append(node('p',`${source.source} · ${source.locator} · ${source.description}`));
        const connected=item.link_current&&item.link.configuration_id===candidate.configuration_id;
        const connect=primary(connected?'이 제품의 자재 계산 →':'이 구성 연결하고 다음 →',async()=>{
          if(!connected)await save('connect',{contract_id:id,interpretation_id:data.interpretation_id,review_id:data.review_id,data_version:data.data_version,item_index:index,configuration_id:candidate.configuration_id});
          await showMaterials(id,index,connected?'':'제품 구성을 연결했습니다. 생산 검토 수량을 확인하고 계산하세요.',review);
        },connected?null:'review');
        if(connect.disabled)connect.title='제품 연결에는 검토 권한이 필요합니다.';
        return [candidate.name,connected?'연결 완료':'조건 일치',details,connect];
      });
      if(rows.length)panel.append(simpleTable(['구성','판정','근거','다음 단계'],rows));
      else if(!missing.length)panel.append(node('p','확인된 계약 조건과 일치하는 등록 제품이 없습니다. 아래 비교 사유를 확인하고 원문 대조 또는 등록된 제품 정보를 확인하세요.','message'));
      if(others.length){
        const comparison=simpleTable(['등록 제품','연결할 수 없는 이유'],others.map(candidate=>{
          const reasons=Object.entries(candidate.comparison).filter(([,value])=>!value.matches).map(([key,value])=>value.contract==null?`${names[key]||key}: 원문 확인 필요`:`${names[key]||key}: 계약 ${value.contract} / 등록 제품 ${value.catalog}`);
          if(!reasons.length)reasons.push('저장된 구성 ID 등 추가 연결 기준을 확인해야 합니다.');
          return [candidate.name,reasons.join(' · ')];
        }));
        panel.append(disclosure(`${missing.length?'확인 대기 중인':'조건이 다른'} 등록 제품 비교 (${others.length}종)`,comparison));
      }
      if(item.link&&!item.link_current)panel.append(node('p','계약 대조 또는 제품 데이터가 바뀌었습니다. 이전 연결을 다시 확인하세요.','message error'));
      connection.append(panel);
    }
  }

  const amount=(value,unit='')=>value===null||value===undefined?'미확인':`${value}${unit?' '+unit:''}`;
  function disclosure(title,content,expanded=false) {
    const section=node('details');section.open=expanded;section.append(node('summary',title),content);return section;
  }
  function bomTable(bom) {
    return simpleTable(['BOM 부품','대당 수량','필요량','공용 가용량','부족량'],(bom.components||[]).map(r=>[
      r.name||r.component_id,...['q','D','A','S'].map(k=>amount(r[k],'개'))]));
  }
  function processTable(rows,reference) {
    const status={unregistered:'기준 미등록',stock_unknown:'재고 미확인',shortage:'부족',covered:'충족'};
    return simpleTable(['공정 자재','총필요량','공용 가용량','부족량','상태 · 사용량 근거'],rows.map(r=>{
      const usage=reference?.materials?.find(m=>m.id===r.material_id)?.usage_basis;
      const basis=node('div');basis.append(node('p',status[r.status]||'확인 필요'));
      if(usage?.base_per_board!=null)basis.append(node('p',`대당 기준: ${amount(usage.base_per_board,usage.unit)}`));
      const proof=node('div');proof.append(node('p',r.basis||'대당 사용량 기준이 등록되지 않았습니다.'));
      const origin=r.basis_origin||usage?.origin;
      if(origin)proof.append(node('p','사용량 자료: '+(String(origin).startsWith('synthetic')?'가상 운영 이력 기반 추정':origin==='derived_conditional'?'공개 치수·조건 기반 추정':'등록 근거 (상세 확인)')));
      for(const ref of r.evidence||[])proof.append(node('p',typeof ref==='string'?ref:JSON.stringify(ref)));
      basis.append(disclosure('대당 기준 · 여유율 · 근거',proof));
      return [r.name,...['required','available','shortage'].map(k=>amount(r[k],r.unit)),basis];
    }));
  }
  function renderCombined(target,saved,filename,allowDownload=true,label='') {
    const d=saved.payload,doc=d.rfq,summary=node('section'),rfq=node('section'),handoff=node('section');rfq.hidden=true;handoff.hidden=true;
    const show=(view)=>{summary.hidden=view!=='summary';rfq.hidden=view!=='rfq';handoff.hidden=view!=='handoff';focusPage(view==='summary'?summary:view==='rfq'?rfq:handoff);};
    target.replaceChildren(summary,rfq,handoff);
    heading(summary,'자재 검토 결과','부족 품목과 미확인 항목을 확인한 다음 RFQ 초안을 확인하세요.');
    const sourceLabel=label||(d.request?`추가 요청 ${d.request.request_id} · 개정 ${d.request.revision}`:d.contract_id?`계약 ${d.contract_id} · 품목 ${d.item_index+1}`:'제품별 자재 검토');
    summary.append(contextBox(sourceLabel,`제품: ${d.configuration_id||d.request?.configuration_id||filename}`,`${d.quantity??d.request?.quantity}대 · 공정 여유율 ${d.allowance_percent}%`,`검토 시각: ${saved.created_at||d.evaluated_at}`));
    const stats=node('div',null,'mat-stats');
    for(const [label,value]of [['검토 수량',`${d.quantity??d.request?.quantity}대`],['확인된 부족 품목',`${doc.lines.length}종`],['RFQ 제외 · 확인 필요',`${doc.excluded.length}종`]]){const card=node('div',null,'mat-stat');card.append(node('span',label),node('strong',value));stats.append(card);}summary.append(stats);
    const rfqButton=primary('RFQ 초안 확인 →',async()=>{show('rfq');await rfqEditor.load();});summary.append(actions(rfqButton));
    summary.append(node('p','저장 당시 재고 기준입니다. 재고·요청 변경 시 다시 계산하세요. 기존 계약과 추가 요청 수량은 자동 합산하지 않습니다.','hint'));
    if(!allowDownload)summary.prepend(node('p','이전 계산 기준이 변경됐습니다. 조건 화면으로 돌아가 다시 계산하세요.','message error'));
    if(doc.excluded.length)summary.append(disclosure(`계산·RFQ에서 확인이 필요한 ${doc.excluded.length}종`,simpleTable(['자재','이유'],doc.excluded.map(r=>[r.name,r.reason]))));
    summary.append(disclosure(`BOM 부품 ${(d.bom.components||[]).length}종 · 필요량과 부족량`,bomTable(d.bom)),disclosure(`공정 자재 ${d.process.length}종 · 필요량과 부족량`,processTable(d.process,d.process_basis)));
    const sources=node('div');
    for(const [kind,scope]of Object.entries(d.stock_scopes||{}))if(scope)sources.append(node('p',`${kind==='bom'?'BOM':'공정'} 재고: ${scope.company} / ${scope.location} · ${scope.as_of}`));
    sources.append(jsonDetails('계산·재고·원문 근거 상세',d));summary.append(disclosure('계산에 사용한 재고와 근거',sources));
    rfq.append(button('← 자재 검토 결과',()=>show('summary')));
    heading(rfq,'RFQ 초안을 검토하고 수정하세요','문구와 견적 요청 내용을 수정한 뒤 저장하세요. 저장한 RFQ가 다운로드와 구매팀 메일 첨부에 사용됩니다.');
    rfq.append(contextBox(sourceLabel,`계산 기준: ${d.quantity??d.request?.quantity}대 · 부족 ${doc.lines.length}종 · 미확인 제외 ${doc.excluded.length}종`));
    rfq.append(node('p','RFQ 문서 수정은 아래 계산 원본과 자재 계산표를 변경하지 않습니다. 이미 저장하거나 보낸 메일의 첨부 파일도 유지됩니다.','hint'));
    const rfqEditor=mountRFQEditor(rfq,saved,filename,allowDownload,async()=>{show('handoff');await refreshHandoff();});
    const refreshHandoff=mountHandoff(handoff,saved,sourceLabel,allowDownload,rfqEditor.download,async()=>{show('rfq');await rfqEditor.load();},rfqEditor.document);
    if(doc.excluded.length)rfq.append(disclosure('계산 원본의 미확인 자재와 제외 이유',simpleTable(['자재','이유'],doc.excluded.map(r=>[r.name,r.reason]))));
  }
  function mountRFQEditor(target,saved,filename,current,onContinue) {
    const source={source_kind:saved.payload.request?'request':'workspace',review_id:saved.id},original=saved.payload.rfq;
    let document=null,saveIntent=null;
    const status=node('p',null,'mat-next'),form=node('form'),editor=field('RFQ 내용','textarea'),controls=node('div'),conflict=node('div');
    editor.input.rows=24;editor.input.maxLength=256*1024;editor.input.value=original.text;editor.input.readOnly=true;
    status.setAttribute('aria-live','polite');conflict.setAttribute('aria-live','polite');
    form.append(editor.label,controls);target.append(status,form,conflict);
    target.append(disclosure('계산으로 생성한 원본 RFQ 보기',node('pre',original.text,'mat-rfq')));
    const canUse=current&&original.lines.length>0,canEdit=canUse&&can('draft');
    const changed=()=>!!document&&editor.input.value!==document.text;
    function updateStatus(){
      status.textContent=!document?'저장된 RFQ를 불러오는 중입니다.':changed()?'저장하지 않은 수정 내용이 있습니다. 저장 후 다운로드하거나 구매팀에 전달하세요.':document.version>0?`저장된 RFQ 수정본 v${document.version} · ${document.edited_by} · ${mailTime(document.edited_at)}`:'계산으로 생성한 RFQ 초안입니다. 내용을 수정하고 저장할 수 있습니다.';
    }
    function selected(){
      if(!document)throw new Error('RFQ를 먼저 불러오세요.');
      if(changed())throw new Error('수정한 RFQ를 먼저 저장하세요.');
      return document;
    }
    async function download(){
      const doc=selected();if(!canUse)throw new Error('현재 계산 기준을 확인하세요.');
      const url=URL.createObjectURL(new Blob([doc.text],{type:'text/plain;charset=utf-8'})),link=node('a');
      link.href=url;link.download=doc.filename||'RFQ-'+String(filename||saved.id).replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,100)+`-v${doc.version}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      notice(`저장한 RFQ ${doc.version>0?'수정본 v'+doc.version:'계산 초안'}의 다운로드를 요청했습니다.`);
    }
    function renderControls(){
      controls.replaceChildren();
      if(canEdit){
        const submit=node('button','RFQ 수정본 저장','mat-primary');submit.type='submit';submit.disabled=!document||!changed();
        const cancel=button('수정 취소',()=>{editor.input.value=document.text;saveIntent=null;conflict.replaceChildren();updateStatus();renderControls();});cancel.disabled=!changed();controls.append(actions(submit,cancel));
      }
      if(canUse){
        const downloadButton=button('RFQ 초안 다운로드 (.txt)',download);downloadButton.disabled=!document||changed();
        const next=primary('구매팀 전달·기록 →',async()=>{selected();await onContinue();});next.disabled=!document||changed();controls.append(actions(downloadButton,next));
      }else controls.append(node('p',original.lines.length?'계산 기준이 변경됐습니다. 다시 계산한 RFQ로 진행하세요.':'확인된 부족 품목이 없어 견적 요청 문서를 저장하거나 전달할 수 없습니다.','message'));
    }
    async function load(){
      if(document)return;
      if(!original.lines.length){status.textContent='확인된 부족 품목이 없습니다.';renderControls();return;}
      try{const result=await call('rfq-document-get',source);document=result.document;editor.input.value=document.text;editor.input.readOnly=!canEdit;updateStatus();renderControls();}
      catch(error){status.textContent='저장된 RFQ를 불러오지 못했습니다.';controls.replaceChildren(actions(button('RFQ 다시 불러오기',load)));throw error;}
    }
    editor.input.addEventListener('input',()=>{updateStatus();renderControls();});
    form.addEventListener('submit',event=>{
      event.preventDefault();if(!canEdit||!document||!changed())return;
      const text=editor.input.value;
      if(!text.trim()||text.includes('\0')||new TextEncoder().encode(text).length>256*1024){notice('RFQ는 비어 있지 않은 256 KiB 이하 텍스트여야 합니다.','error');return;}
      const body={...source,base_document_id:document.document_id,text};
      if(!saveIntent||canonical(saveIntent.body)!==canonical(body))saveIntent={body,key:crypto.randomUUID()};
      run(()=>attempt(async()=>{
        try{
          const result=await call('rfq-document-save',{...body,request_key:saveIntent.key});document=result.document;saveIntent=null;editor.input.value=document.text;conflict.replaceChildren();updateStatus();renderControls();notice(`RFQ 수정본 v${document.version}을 저장했습니다. 이 수정본을 다운로드하거나 메일에 첨부할 수 있습니다.`);
        }catch(error){
          if(error.response?.errors?.some(e=>e.code==='RFQ_DOCUMENT_CONFLICT')){
            conflict.replaceChildren(node('p','다른 수정본이 먼저 저장됐습니다. 입력한 내용은 그대로 유지했습니다. 최신본을 확인한 뒤 이어서 수정하세요.','message'));
            conflict.append(actions(button('내 입력 내용 복사',async()=>{await navigator.clipboard.writeText(editor.input.value);notice('현재 RFQ 입력 내용을 복사했습니다.');}),button('최신 저장본 비교',async()=>{
              const latest=(await call('rfq-document-get',source)).document;
              const comparison=node('section',null,'mat-choice');comparison.append(node('h3',`최신 RFQ 수정본 v${latest.version}`),node('pre',latest.text,'mat-rfq'),actions(button('최신본으로 편집 시작',()=>{document=latest;editor.input.value=latest.text;saveIntent=null;conflict.replaceChildren();updateStatus();renderControls();notice('최신 저장본을 편집기에 불러왔습니다.');})));
              conflict.append(comparison);
            })));
          }
          throw error;
        }
      }));
    });
    renderControls();return {load,document:selected,download};
  }
  function renderHandoffItems(historyItems,items,result,refresh) {
    const graphLabels={file_saved:'증빙 파일 보관 · 그래프 등록 재시도 필요',pending_review:'검토·승인 대기',changes_requested:'보완 요청',rejected:'반려',approved_pending_apply:'승인됨 · 그래프 반영 확인 필요',applied:'그래프 반영 확인됨',lookup_failed:'그래프 상태 확인 실패'};
    historyItems.replaceChildren();
    if(!items.length){historyItems.append(node('p','이 RFQ에 등록된 전달 기록이 없습니다.','empty-row'));return;}
    for(const item of items){
      const card=node('article',null,'mat-choice');
      card.append(contextBox(`제품: ${item.rfq?.configuration_id||'원문 참조'} · RFQ 검토 번호: ${item.review_id}`));
      card.append(node('h4',item.subject),node('p',`전달 기록: ${item.evidence_origin==='synthetic'?'시연용 가상 기록':'담당자 제출 증빙'} · ${item.recipient}`),node('p',`전달 시각: ${item.sent_at} · 등록자: ${item.reported_by}`),node('p',`그래프: ${graphLabels[item.graph_status]||'확인 필요'}`));
      card.append(node('p','메일 서비스의 발송·수신 확인 결과가 아닌 담당자가 제출한 전달 기록입니다.','hint'));
      card.append(disclosure('보관한 메일 내용·증빙 정보',contextBox(item.message,`증빙: ${item.evidence_filename}`,`증빙 SHA-256: ${item.evidence_sha256}`,`RFQ SHA-256: ${item.rfq_sha256}`)));
      if(item.draft?.draft_id){
        const downloads=node('div',null,'mat-actions');
        for(const [suffix,label]of [['_rfq','전달한 RFQ 원본 내려받기'],['_attachment','보관한 증빙 내려받기']]){
          const source=item.draft.operations?.find(op=>op.node?.type==='SourceDocument'&&op.node.kind?.endsWith(suffix))?.node;
          if(source){const link=node('a',label);link.href='/api/draft-source?'+new URLSearchParams({draft_id:item.draft.draft_id,version:String(item.draft.draft_version),source_id:source.id});link.target='_blank';link.rel='noopener';downloads.append(link);}
        }
        card.append(downloads);
      }
      if(item.draft?.draft_id)card.append(actions(button('등록안 검토 화면으로',async()=>{selectView('review');await loadDraft({id:item.draft.draft_id,version:item.draft.draft_version});})));
      if(item.graph_status==='file_saved'){
        card.append(node('p','메일을 다시 보내지 말고, 기존 증빙 내용으로 그래프 등록을 재시도하세요.','message'));
        if(item.can_retry)card.append(actions(button('보관한 증빙으로 그래프 등록 재시도',async()=>{
          const response=must(await api('/api/planning/rfq-handoff-retry',{source_kind:item.source_kind,review_id:item.review_id,receipt_id:item.receipt_id}));
          if(response.data.draft)showBusinessDraft(response.data.draft,result);
          notice('보관된 증빙의 그래프 등록안을 만들었습니다. 메일 재발송은 하지 않았습니다.');await refresh();
        },'draft')));
        else card.append(node('p','이 증빙을 등록한 담당자가 같은 기록으로 재시도할 수 있습니다.','hint'));
      }
      historyItems.append(card);
    }
  }

  const mailStatus = item => ({draft:'초안 저장됨 · 보내기 전',local_captured:'시험 메일함에 보관됨',smtp_accepted:'메일 서버 접수됨 · 수신 확인 안 됨',rejected:'전송 실패 · 메일 서버 미접수',unknown:'전송 결과 확인 필요 · 자동 재전송 안 함'}[item.status]||'상태 확인 필요');
  const mailTime = value => value ? new Date(value).toLocaleString('ko-KR',{timeZoneName:'short'}) : '—';
  async function downloadMail(item,kind) {
    const file=await call('mail-file',{draft_id:item.draft_id,kind});
    const raw=Uint8Array.from(atob(file.content_base64),c=>c.charCodeAt(0));
    const url=URL.createObjectURL(new Blob([raw],{type:file.mime})),link=node('a');
    link.href=url;link.download=file.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    notice('보관한 원본 파일의 다운로드를 요청했습니다.');
  }
  function renderMailItem(target,item,refresh,{send=false,edit=null}={}) {
    target.replaceChildren();target.setAttribute('aria-live','polite');
    const graphLabels={file_saved:'메일 기록 보관됨 · 그래프 등록 재시도 필요',pending_review:'등록안 생성됨 · 검토·승인 대기',changes_requested:'보완 요청',rejected:'반려',approved_pending_apply:'승인됨 · 반영 확인 필요',applied:'그래프 반영 확인됨',lookup_failed:'그래프 상태 확인 실패'};
    const local=!item.transport?.external;
    target.append(node('h3',item.subject),node('p',mailStatus(item),'mat-next'));
    target.append(contextBox(`받는 사람: ${item.recipient}`,`보내는 사람: ${typeof item.sender==='object'?`${item.sender.name} <${item.sender.email}>`:(item.sender||'서비스 메일 계정')}`,`초안 저장: ${mailTime(item.prepared_at)}`,`RFQ 검토 번호: ${item.review_id}`));
    const body=node('pre',item.message,'mat-rfq');body.setAttribute('aria-label','저장한 메일 본문');target.append(body);
    if(item.attachment)target.append(contextBox(`자동 첨부: ${item.attachment.filename} · ${item.attachment.byte_length.toLocaleString()} bytes${item.attachment.version>0?' · RFQ 수정본 v'+item.attachment.version:''}`, `RFQ SHA-256: ${item.attachment.sha256}`));
    target.append(actions(button('첨부 RFQ 내려받기',()=>downloadMail(item,'rfq'))));
    if(item.status==='draft'){
      target.append(node('p','보내기를 누르면 전송 시각과 보낸 내용·RFQ 파일을 자동 보관합니다.','hint'));
      const controls=[];
      if(edit)controls.push(button('← 메일 수정',()=>edit(item),'draft'));
      if(send&&item.can_send)controls.push(primary(local?'시험 메일함으로 보내기':'구매팀에 메일 보내기',async()=>{
        let result;
        try{result=await call('mail-send',{draft_id:item.draft_id});}
        catch(error){
          // Recover the same attempt after a lost response. Never create a new draft or retry transmission here.
          try{const saved=await call('mail-get',{draft_id:item.draft_id});renderMailItem(target,saved.item,refresh,{send,edit});await refresh();}catch{/* Keep the same draft id available for the next explicit status read. */}
          throw error;
        }
        renderMailItem(target,result.item,refresh,{send:false});await refresh();
        notice(mailStatus(result.item),['unknown','rejected'].includes(result.item.status)?'error':'');focusPage(target);
      },'draft'));
      target.append(actions(...controls));
      if(send&&!item.can_send)target.append(node('p','이 초안을 작성한 담당자가 전송할 수 있습니다.','hint'));
    }else{
      target.append(contextBox(`전송 시도: ${mailTime(item.attempted_at)}`,`접수 시각: ${mailTime(item.sent_at)}`));
      if(item.message_available!==false)target.append(actions(button('보관한 메일 원본 (.eml)',()=>downloadMail(item,'mail'))));
      if(item.status==='unknown')target.append(node('p','같은 메일을 다시 보내지 않습니다. 메일 서버 기록을 확인하세요.','message'));
      if(item.status==='rejected')target.append(node('p',item.error_message||'메일 서버에 접수되지 않았습니다. 연결 설정과 수신 주소를 확인하세요.','message error'));
    }
    const handoff=item.handoff,graphStatus=item.graph_status||handoff?.graph_status;
    if(graphStatus&&graphStatus!=='not_created'){
      target.append(node('p',`그래프: ${graphLabels[graphStatus]||'확인 필요'}`));
      if(handoff?.draft?.draft_id)target.append(actions(button('등록안 검토 화면으로',async()=>{selectView('review');await loadDraft({id:handoff.draft.draft_id,version:handoff.draft.draft_version});})));
      if(graphStatus==='file_saved'&&item.can_retry_graph)target.append(actions(button('그래프 등록 재시도',async()=>{
        const result=await call('mail-graph-retry',{draft_id:item.draft_id});renderMailItem(target,result.item,refresh);await refresh();notice('보관한 메일로 그래프 등록을 재시도했습니다. 메일을 다시 보내지 않았습니다.');
      },'draft')));
      target.append(node('p','메일 기록은 자동 보관됩니다. 그래프 반영은 기존 등록안 검토·승인 후 확인합니다.','hint'));
    }
    target.append(actions(button('이 메일 상태 다시 확인',async()=>{const saved=await call('mail-get',{draft_id:item.draft_id});renderMailItem(target,saved.item,refresh,{send,edit});await refresh();})));
  }
  function renderMailList(target,data,open) {
    target.replaceChildren();
    if(!data.items.length){target.append(node('p','저장한 메일이 없습니다. RFQ 결과에서 구매팀 전달·기록을 열어 메일을 준비하세요.','empty-row'));return;}
    for(const item of data.items){
      const card=node('article',null,'mat-choice'),head=node('div',null,'mat-choice-head'),info=node('div');
      info.append(node('h3',item.subject),node('p',`${item.recipient} · ${mailStatus(item)}`),node('p',`${mailTime(item.sent_at||item.prepared_at)} · RFQ ${item.review_id}`,'hint'));
      head.append(info,button('메일 열기',()=>open(item)));card.append(head);target.append(card);
    }
  }
  function mountHandoff(target,saved,sourceLabel,current,download,back,getDocument) {
    const d=saved.payload,doc=d.rfq,reference={source_kind:d.request?'request':'workspace',review_id:saved.id};
    target.append(button('← RFQ 초안',back));
    heading(target,'구매팀에 RFQ 전달','구매팀 선택 → 메일 수정 → 첨부·내용 검토 → 보내기. 전송 시각과 메일·첨부 파일은 자동 보관합니다.');
    target.append(contextBox(sourceLabel,`계산 기준: ${d.quantity??d.request?.quantity}대 검토 · 부족 ${doc.lines.length}종 · 미확인 제외 ${doc.excluded.length}종`));
    const compose=node('section'),preview=node('section'),history=node('section'),rows=node('div');preview.hidden=true;
    const form=node('form'),fields=node('fieldset'),recipientLabel=node('label','구매팀 이메일'),recipient=node('select');recipient.required=true;recipient.setAttribute('aria-label','구매팀 이메일');recipientLabel.append(recipient);
    const subject=field('메일 제목'),message=field('메일 본문','textarea');subject.input.required=true;subject.input.maxLength=300;message.input.required=true;message.input.maxLength=8000;message.input.rows=10;
    const attachment=node('div',null,'mat-context'),composeStatus=node('div');composeStatus.setAttribute('aria-live','polite');
    const submit=node('button','첨부·내용 검토 →','mat-primary');submit.type='submit';
    fields.append(node('legend','1. 메일 작성'),recipientLabel,subject.label,message.label,attachment,actions(submit));form.append(fields);compose.append(composeStatus,form);
    const result=node('div');if(writableCompose())preview.append(button('← 메일 작성 화면',()=>{compose.hidden=false;preview.hidden=true;focusPage(compose);}));preview.append(result);
    function writableCompose(){return can('draft')&&current&&doc.lines.length>0;}
    const previous=node('details'),legacyRows=node('div'),legacyResult=node('div');previous.append(node('summary','이전에 수동으로 등록한 전달 증빙'),legacyRows,legacyResult);
    history.append(node('h3','이 RFQ의 메일 기록'),actions(button('메일 기록 새로고침',()=>refreshHistory())),rows,previous);target.append(compose,preview,history);
    const writable=can('draft')&&current&&doc.lines.length>0;fields.disabled=!writable;
    if(!writable)compose.prepend(node('p',!can('draft')?'메일 작성에는 초안 작성 권한이 필요합니다.':!current?'계산 기준이 변경됐습니다. 다시 계산한 RFQ로 메일을 준비하세요.':'확인된 부족 품목이 없어 새 RFQ 메일을 만들 수 없습니다.','message'));
    let config=null,loadedDocument=null,dirty=false,draftIntent=null;
    function edit(item){recipient.value=item.contact_id;subject.input.value=item.subject;message.input.value=item.message;dirty=true;compose.hidden=false;preview.hidden=true;focusPage(compose);}
    async function open(item,canSend=writable){const data=await call('mail-get',{draft_id:item.draft_id});compose.hidden=true;preview.hidden=false;renderMailItem(result,data.item,refreshHistory,{send:canSend,edit:writable?edit:null});focusPage(preview);}
    async function refreshHistory(){
      const data=await call('mail-history',{...reference,offset:0});renderMailList(rows,data,item=>open(item));
      if(data.total>data.items.length)rows.append(node('p',`최근 ${data.items.length}건입니다. 전체 기록은 상단 ‘구매팀 전달 기록’에서 확인하세요.`,'hint'));
    }
    previous.addEventListener('toggle',()=>{if(previous.open)run(()=>attempt(async()=>{const data=await call('rfq-handoff-status',reference);renderHandoffItems(legacyRows,(data.items||[]).filter(i=>!i.automated),legacyResult,refreshHistory);}));});
    for(const input of [recipient,subject.input,message.input])input.addEventListener('input',()=>{dirty=true;});
    form.addEventListener('submit',event=>{
      event.preventDefault();if(!writable||!form.reportValidity())return;
      try{if(!loadedDocument||loadedDocument!==getDocument().document_id)throw new Error('선택한 RFQ 첨부를 다시 불러온 뒤 메일을 검토하세요.');}
      catch(error){notice(error.message,'error');return;}
      const body={...reference,rfq_document_id:loadedDocument,contact_id:recipient.value,subject:subject.input.value.trim(),message:message.input.value};
      if(!body.subject||!body.message.trim()){notice('메일 제목과 본문을 입력하세요.','error');return;}
      run(()=>attempt(async()=>{
        // One save intent survives a lost response. A later deliberate composition gets a new draft.
        if(!draftIntent||canonical(draftIntent.body)!==canonical(body))draftIntent={body,key:crypto.randomUUID()};
        const data=await call('mail-save',{...body,request_key:draftIntent.key});draftIntent=null;dirty=false;await open(data.item);await refreshHistory();notice('초안을 저장했습니다. 받는 사람과 자동 첨부 파일을 확인한 뒤 보내세요.');
      }),submit);
    });
    return async function refresh(){
      const selected=getDocument();
      if(loadedDocument!==selected.document_id&&doc.lines.length){
        const previousContact=recipient.value;
        fields.disabled=true;loadedDocument=null;config=null;compose.hidden=false;preview.hidden=true;result.replaceChildren();
        attachment.replaceChildren(node('p','선택한 RFQ 첨부를 불러오는 중입니다.'));composeStatus.replaceChildren();
        try{config=await call('mail-compose',{...reference,rfq_document_id:selected.document_id});}
        catch(error){composeStatus.append(node('p','RFQ 첨부 정보를 불러오지 못했습니다. 메일 입력은 유지했으며 다른 버전의 RFQ로 진행하지 않습니다.','message error'),actions(button('첨부 정보 다시 불러오기',refresh)));throw error;}
        recipient.replaceChildren();
        for(const c of config.contacts){const option=node('option',`${c.name} · ${c.email}`);option.value=c.id;recipient.append(option);}
        if(config.contacts.some(c=>c.id===previousContact))recipient.value=previousContact;
        if(!dirty){subject.input.value=config.suggested.subject;message.input.value=config.suggested.message;}
        attachment.replaceChildren(node('strong',`자동 첨부: ${config.attachment.filename}`),node('p',`${config.attachment.byte_length.toLocaleString()} bytes · ${selected.version>0?'저장한 RFQ 수정본 v'+selected.version:'계산 RFQ 원본'}`),actions(button('첨부할 RFQ 미리 보기',download)));
        loadedDocument=selected.document_id;draftIntent=null;fields.disabled=!writable;
        if(dirty)notice('첨부 RFQ가 변경됐습니다. 입력한 메일 내용을 유지했으니 첨부와 함께 다시 검토하세요.');
      }
      await refreshHistory();
    };
  }
  function numberField(label,value,min,max,step) {
    const f=field(label,'number',value);Object.assign(f.input,{required:true,min:String(min),max:String(max),step:String(step)});return f;
  }
  function contractQuantityDefaults(data,review) {
    const check=[...(review?.checks||[])].reverse().find(row=>row.decisions);
    const sameBasis=review?.id===data.link.interpretation_id&&check?.id===data.link.review_id;
    const source=sameBasis?review.result.items[data.item_index]?.fields||{}:{};
    const fields=data.fields||{};
    const read=name=>{
      const f=fields[name]||{},raw=source[name]||{};
      const confirmed=['accepted','corrected'].includes(f.state);
      const suggested=!confirmed&&!f.reason&&raw.state==='supported'&&raw.claims?.some(c=>c.evidence?.some(e=>e.quote));
      return {name,value:confirmed?f.value:suggested?raw.value:null,
        status:confirmed?(f.state==='corrected'?'원문 확인 후 수정':'원문 확인 완료'):suggested?'원문 추출 · 미확인':'미확인',
        specified:confirmed||!!f.reason||!!suggested||!!raw.claims?.length||['conflict','invalid','unsupported'].includes(raw.state),evidence:raw.claims||[],reason:f.reason||''};
    };
    const integer=value=>{
      if(typeof value!=='number'&&!(typeof value==='string'&&/^\d+$/.test(value)))return null;
      const n=Number(value);return Number.isSafeInteger(n)&&n>=0&&n<=10000000?n:null;
    };
    const quantities=['ordered_quantity','planned_input_quantity','fulfilled_quantity','allocated_quantity'].map(name=>{
      const row=read(name);return {...row,quantity:integer(row.value)};
    });
    const [order,planned]=quantities,unit=read('unit'),boardUnit=['piece','대'].includes(unit.value);
    const selected=planned.specified?planned:order;
    const currentSaved=(data.combined_current?data.combined_saved?.payload:null)??(data.saved_current?data.saved:null);
    const saved=currentSaved??data.combined_saved?.payload??data.saved;
    const value=saved?.quantity??(boardUnit?selected.quantity:null);
    const initialReason=saved?.reason??(value==null?'':`계약 품목 ${data.item_index+1}의 ${names[selected.name]} ${value}대 (${selected.status}) 기준으로 자재 검토.${selected.name==='ordered_quantity'?' 기납품·기배정 차감 전 전체 주문 기준.':''}`);
    return {quantities,unit,boardUnit,value,initialReason,saved,currentSaved,selected};
  }

  async function showMaterials(id,index,completed='',review=null) {
    const data=await call('materials',{contract_id:id,item_index:index});conditions.replaceChildren(button('← 제품 연결',()=>open(id,2)));
    heading(conditions,'생산 수량을 확인하고 계산하세요','생산 검토 수량을 한 번 입력하면 BOM과 공정 자재에 함께 적용합니다.');
    conditions.append(contextBox(data.title,`계약 품목 ${index+1} · ${data.link.configuration_id}`));
    const defaults=contractQuantityDefaults(data,review);
    conditions.append(simpleTable(['계약 수량 기준','수량','확인 상태'],defaults.quantities.map(row=>[
      names[row.name],row.quantity==null?'미확인':`${row.quantity.toLocaleString('ko-KR')} ${defaults.boardUnit?'대':defaults.unit.value||'(단위 미확인)'}`,row.status])));
    const proof=node('div');
    for(const row of defaults.quantities){
      const refs=row.evidence.flatMap(claim=>claim.evidence||[]);
      if(refs.length){proof.append(node('h4',names[row.name]));for(const ref of refs)proof.append(node('blockquote',`${ref.quote} · ${ref.segment_id}`));}
      if(row.reason)proof.append(node('p',`${names[row.name]} 검토 사유: ${row.reason}`));
    }
    conditions.append(disclosure('계약 수량 원문·수정 근거',proof));
    const defaultHint=defaults.currentSaved?'현재 저장된 검토 수량과 산정 근거를 불러왔습니다.':defaults.saved?`이전 검토 수량 ${defaults.value}대와 근거를 유지했습니다. 계약 또는 제품 연결이 변경됐으므로 현재 계약 수량과 대조하고 다시 계산하세요.`:defaults.value==null?'자동으로 채울 수 있는 수량 또는 완제품 단위가 없습니다. 원문을 확인하고 검토 수량을 입력하세요.':`${names[defaults.selected.name]} ${defaults.value}대를 초기값으로 넣었습니다 (${defaults.selected.status}). 계산 전에 확인하고 필요하면 수정하세요.`;
    conditions.append(node('p',defaultHint,'mat-next'));
    const form=node('form'),grid=node('div',null,'mat-grid'),quantity=numberField('생산 검토 수량 (대)',defaults.value??'',0,10000000,1),allowance=numberField('공정 자재 여유율 (%)',defaults.saved?.allowance_percent??'10',0,100,.01),reason=field('수량 산정 근거','textarea',defaults.initialReason);
    reason.input.required=true;reason.input.maxLength=2000;reason.label.className='wide';reason.input.rows=3;
    const submit=node('button','계산하고 결과 확인 →','mat-primary');submit.type='submit';submit.disabled=!can('review');
    grid.append(quantity.label,allowance.label,reason.label);form.append(grid,node('p','주문 수량에서 생산할 수량은 기존 납품·배정 기록을 확인하여 정하세요.','hint'),actions(submit));conditions.append(form);
    const allowanceGuide=node('div');
    allowanceGuide.append(node('p','10%는 조정 가능한 초기 계획 여유율이며, 제조 표준이나 실측 손실률이 아닙니다.'),node('p','공정 자재 필요량 = 대당 기준 사용량 × 생산 검토 수량 × (1 + 여유율 / 100) + 작업 준비량'),node('p','가상 이력 기반 대당 사용량에는 일부 손실이 이미 포함돼 있습니다. 여유율은 추가 계획 여유분이고, 준비량은 등록된 경우 작업당 한 번 더합니다. 생산 대수와 BOM 부품 수량에는 이 여유율을 적용하지 않습니다.'));
    allowance.label.append(node('span','추가 계획 여유분 · 기본 10%는 임시 설정','hint'));
    conditions.append(disclosure('공정 자재 여유율의 의미와 계산식',allowanceGuide));
    if(!defaults.saved)quantity.input.addEventListener('input',()=>{if(reason.input.value===defaults.initialReason)reason.input.value='';});
    let savedButton;
    const displayResult=(saved,fresh=true)=>{contractResult.replaceChildren(button('← 계산 조건 수정',()=>contractFlow.show(3)));const body=node('div');contractResult.append(body);renderCombined(body,saved,'contract-'+id+'-'+index,fresh,`${data.title} · 품목 ${index+1}`);};
    for(const input of [quantity.input,allowance.input,reason.input])input.addEventListener('input',()=>{contractResult.replaceChildren();if(savedButton)savedButton.hidden=true;notice('조건이 변경됐습니다. 계산 버튼을 눌러 새 결과를 확인하세요.','pending');});
    form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;run(()=>attempt(async()=>{
      notice('BOM과 공정 자재의 필요량·부족량을 계산하고 있습니다.','pending');
      const saved=await reviewSave('contract-combined-review',{contract_id:id,item_index:index,link_id:data.link.id,quantity:Number(quantity.input.value),reason:reason.input.value,allowance_percent:allowance.input.value});
      displayResult(saved);contractFlow.show(4,'계산 완료 · 자재 검토 결과와 RFQ 초안을 저장했습니다.');
    }),submit);});
    if(data.combined_saved){
      const fresh=data.combined_current&&!data.combined_stock_changed;
      displayResult(data.combined_saved,fresh);
      savedButton=button(fresh?'저장된 검토 결과 보기 →':'이전 검토 결과 보기 (재계산 필요)',()=>contractFlow.show(4));conditions.append(actions(savedButton));
      if(!fresh)conditions.append(node('p','이전 검토 이후 제품 연결·데이터 또는 재고가 바뀌었습니다. 다시 계산하세요.','message'));
    }
    if(data.stock)conditions.append(node('p',`${data.stock.label} · ${data.stock.origin==='synthetic'?'가상 운영 기록':'자료 구분 확인 필요'} · ${data.stock.scope.location} · ${data.stock.scope.as_of}`,'hint'));
    else conditions.append(node('p','BOM 재고 자료 미연결: 가용량과 부족량은 미확인으로 남습니다.','message'));
    contractFlow.show(3,completed);
  }
  function mountProducts() {
    const f=flow(products,['수량 입력','제품 비교','검토 결과']),[inputPage,comparePage,resultPage]=f.pages;
    heading(inputPage,'몇 대를 생산할지 입력하세요','한 번 입력한 수량으로 등록된 모든 제품의 BOM과 공정 자재를 비교합니다.');
    const form=node('form'),grid=node('div',null,'mat-grid'),quantity=numberField('비교할 생산 수량 (대)','',0,10000000,1),allowance=numberField('공정 자재 여유율 (%)','10',0,100,.01);
    const submit=node('button','제품별 필요량 비교 →','mat-primary');submit.type='submit';grid.append(quantity.label,allowance.label);form.append(grid,actions(submit));inputPage.append(form,node('p','각 제품을 공용 재고와 개별 비교합니다. 여러 제품의 수량을 합산하거나 재고를 배정하지 않습니다.','hint'));
    const reset=()=>{comparePage.replaceChildren();resultPage.replaceChildren();notice('조건이 변경됐습니다. 제품별 필요량 비교를 다시 실행하세요.','pending');};
    for(const input of [quantity.input,allowance.input])input.addEventListener('input',reset);
    form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;run(()=>attempt(async()=>{
      notice('제품과 자재 기준을 조회하고 있습니다.','pending');
      const n=Number(quantity.input.value),margin=allowance.input.value,data=await call('product-inventory',{quantity:n});
      const previews=await Promise.all(data.products.map(p=>call('process-requirement-preview',{configuration_id:p.configuration_id,quantity:n,allowance_percent:margin})));
      comparePage.replaceChildren(button('← 생산 수량 수정',()=>f.show(0)));
      heading(comparePage,'검토할 제품을 선택하세요','제품별 필요량을 펼쳐 비교하고, 원하는 제품의 검토 결과를 만드세요.');
      comparePage.append(contextBox(`${n}대 · 공정 자재 여유율 ${margin}% · 등록 제품 ${data.products.length}종`));
      if(!data.products.length)comparePage.append(node('p','조회된 제품이 없습니다. 제품 구성이 등록됐는지 확인하세요.','empty-row'));
      data.products.forEach((product,i)=>{
        const panel=node('section',null,'mat-choice'),head=node('div',null,'mat-choice-head'),label=node('div');
        const bom=product.result.components||[],proc=previews[i].lines;
        label.append(node('h3',product.name),node('p',`부족: BOM ${bom.filter(r=>r.S>0).length}종 · 공정 자재 ${proc.filter(r=>r.shortage>0).length}종`),node('p',product.configuration_id,'hint'));
        head.append(label,primary('이 제품 검토하고 결과 보기 →',async()=>{
          notice('선택한 제품의 현재 재고를 확인하고 검토 결과를 저장합니다.','pending');
          const saved=await reviewSave('combined-review',{configuration_id:product.configuration_id,quantity:n,allowance_percent:margin});
          resultPage.replaceChildren(button('← 제품 비교로',()=>f.show(1)));const body=node('div');resultPage.append(body);renderCombined(body,saved,product.configuration_id);
          f.show(2,`${product.name} ${n}대 검토 완료 · 결과와 RFQ 초안을 저장했습니다.`);
        },'review'));
        const body=node('div');body.append(disclosure('BOM 부품별 필요량',bomTable(product.result)),disclosure('공정 자재별 필요량',processTable(proc),true));
        panel.append(head,disclosure('BOM·공정 자재 필요량 펼치기',body));comparePage.append(panel);
      });f.show(1,`${n}대 기준으로 ${data.products.length}개 제품을 비교했습니다. 검토할 제품을 선택하세요.`);
    }),submit);});
  }
  function contractEvidence(row) {
    const c=row.contract_context,content=node('div');
    if(!c?.contract){content.append(node('p','기존 계약 연결이 필요합니다. 요청을 등록한 쪽에서 계약 품목 정보를 보완하세요.'));return content;}
    content.append(node('p',`고객: ${c.contract.customer_name||'미확인'} · 회사: ${c.contract.company||'미확인'}`),node('p',`계약: ${c.contract.id} / 품목: ${c.item.id}`),node('p',`기존 계약 ${c.item.quantity}대 · 이번 추가 요청 ${row.request.quantity}대. 이번 계산에는 추가 요청량만 적용합니다.`));
    for(const ev of c.evidence||[])content.append(node('p',`${ev.id} · ${ev.locator}`));
    for(const doc of c.documents||[]){
      content.append(node('p',`${doc.source.id} · SHA-256 ${doc.source.sha256}`,'hint'));
      if(doc.text!=null){const text=node('pre',doc.text);text.style.whiteSpace='pre-wrap';text.style.overflowWrap='anywhere';content.append(node('p','해시를 확인한 작성 당시 계약 원문입니다.'),text);}
      else content.append(node('p',doc.status==='hash_mismatch'?'원문 해시 불일치로 내용을 표시하지 않습니다.':'현재 서버에서 원문 내용을 표시할 수 없습니다.'));
    }return content;
  }
  function mountRequests() {
    const f=flow(requests,['요청 선택','내용 확인','검토 결과']),[listPage,checkPage,resultPage]=f.pages;
    heading(listPage,'추가 생산 요청을 선택하세요','요청의 기존 계약·제품·추가 수량을 확인한 뒤 자재 검토로 진행합니다.');
    const results=node('div');let loaded=false;
    const status={needs_info:'보완 필요',reviewed:'검토 이력 있음',seen:'확인함',new:'새 요청'};
    async function refresh(){
      notice('새 요청을 조회하고 있습니다.','pending');
      const data=await call('request-inbox',{});results.replaceChildren(node('p',`최신 요청 ${data.requests.length}건 · 요청 그래프 v${data.data_version}`,'hint'));
      for(const bad of data.invalid||[])results.append(node('p',bad.message,'message error'));
      if(!data.requests.length)results.append(node('p','등록된 추가 생산 요청이 없습니다. 새 요청이 등록되면 목록 새로고침으로 확인하세요.','empty-row'));
      for(const row of data.requests){
        const p=row.request,card=node('section',null,'mat-choice'),head=node('div',null,'mat-choice-head'),label=node('div'),badge=node('span',status[row.status]||row.status,'pill '+(row.issues?.length?'needs_confirmation':'neutral'));
        label.append(badge,node('h3',`${p.quantity}대 추가 생산 요청`),node('p',p.configuration_id),node('p',`${p.need_date} · ${{production_complete:'생산 완료',dispatch:'출고',customer_arrival:'고객 도착'}[p.need_basis]||p.need_basis}`),node('p',`${p.request_id} · 개정 ${p.revision}`,'hint'));
        head.append(label,primary('요청 내용 확인 →',async()=>{
          checkPage.replaceChildren(button('← 요청 목록',()=>f.show(0)));
          heading(checkPage,'요청 내용과 계산 조건을 확인하세요','추가 요청 수량은 등록된 값을 사용합니다. 여유율을 확인하고 계산하세요.');
          checkPage.append(contextBox(`${p.quantity}대 · ${p.configuration_id}`,`기존 계약: ${row.contract?.id||'연결 필요'} / 품목: ${p.contract_item_id||'미연결'}`,`필요일: ${p.need_date} · ${{production_complete:'생산 완료',dispatch:'출고',customer_arrival:'고객 도착'}[p.need_basis]||p.need_basis}`));
          checkPage.append(disclosure('요청 사유 · 조건 · 기존 계획 근거',contextBox(p.reason,`조건: ${p.conditions_note}`,`기존 계획 근거: ${p.baseline_evidence_id}`,`${p.request_id} · 개정 ${p.revision}`,p.origin==='synthetic'?'가상 운영 기록':'실제 운영 기록')),disclosure('기존 계약 원문과 연결 근거',contractEvidence(row)));
          const form=node('form'),allowance=numberField('공정 자재 여유율 (%)','10',0,100,.01),submit=node('button','계산하고 결과 확인 →','mat-primary');submit.type='submit';submit.disabled=!can('review')||!!row.issues?.length||!!data.invalid?.length;
          for(const issue of row.issues||[])checkPage.append(node('p',issue,'message error'));
          if(data.invalid?.length)checkPage.append(node('p','요청 목록에 검증 오류가 있습니다. 원본 요청을 보완한 뒤 목록을 새로고침하세요.','message error'));
          if(row.issues?.length)checkPage.append(node('p','요청 등록 담당자가 위 정보를 보완해야 계산할 수 있습니다. 요청 목록으로 돌아가 다른 요청을 확인할 수 있습니다.','mat-next'));
          if(row.last_review)checkPage.append(node('p','이전에 검토한 요청입니다. 이번에는 현재 재고와 여유율로 다시 계산합니다.','hint'));
          form.append(allowance.label,actions(submit));checkPage.append(form,node('p','이번 계산에는 추가 요청량만 적용합니다. 요청을 읽는 것은 자재 검토 완료가 아닙니다.','hint'));
          allowance.input.addEventListener('input',()=>{resultPage.replaceChildren();notice('여유율이 변경됐습니다. 다시 계산해 결과를 확인하세요.','pending');});
          form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;run(()=>attempt(async()=>{
            notice('요청 수량의 자재 필요량·부족량을 계산하고 있습니다.','pending');
            const saved=await reviewSave('request-review',{evidence_id:row.evidence_id,data_version:data.data_version,allowance_percent:allowance.input.value});
            resultPage.replaceChildren(button('← 요청·계산 조건',()=>f.show(1)));const body=node('div');resultPage.append(body);renderCombined(body,saved,p.request_id+'-r'+p.revision);
            row.status='reviewed';badge.textContent='검토 이력 있음';f.show(2,`${p.quantity}대 추가 요청 검토 완료 · 결과와 RFQ 초안을 저장했습니다.`);
          }),submit);});
          f.show(1);
          // Opening a request records only read status. No material review is implied.
          await call('request-seen',{evidence_id:row.evidence_id});if(row.status==='new'){row.status='seen';badge.textContent='확인함';}
          focusPage(checkPage); // Apply focus again after the read operation releases the inert screen.
        }));card.append(head);results.append(card);
      }loaded=true;f.show(0,`요청 ${data.requests.length}건을 조회했습니다. 내용을 확인할 요청을 선택하세요.`);
    }
    listPage.append(actions(button('목록 새로고침',refresh)),results);
    loadRequests=async()=>{if(!loaded)await refresh();};
  }
  function mountHandoffHistory() {
    heading(handoffHistory,'구매팀 전달 기록','저장한 초안과 보낸 메일을 다시 열 수 있습니다. 전송 당시 내용·RFQ 파일·시각과 그래프 반영 상태를 보관합니다.');
    let offset=0;
    const list=node('section'),detail=node('section'),opened=node('div'),rows=node('div'),navigation=node('div');detail.hidden=true;
    detail.append(button('← 메일 기록 목록',()=>{detail.hidden=true;list.hidden=false;focusPage(list);}),opened);
    list.append(actions(button('메일 기록 목록 새로고침',()=>{offset=0;return refresh();})),rows,navigation);
    const legacy=node('details'),legacyRows=node('div'),legacyResult=node('div');legacy.append(node('summary','기존 수동 전달 증빙 조회'),legacyRows,legacyResult);list.append(legacy);
    let legacyOffset=0;
    async function legacyRefresh(){
      const data=await call('rfq-handoff-history',{offset:legacyOffset});renderHandoffItems(legacyRows,data.items.filter(i=>!i.automated),legacyResult,legacyRefresh);
      const controls=[];if(legacyOffset>0)controls.push(button('이전 증빙',()=>{legacyOffset=Math.max(0,legacyOffset-data.limit);return legacyRefresh();}));
      if(legacyOffset+data.limit<data.total)controls.push(button('다음 증빙',()=>{legacyOffset+=data.limit;return legacyRefresh();}));legacyRows.append(actions(...controls));
    }
    legacy.addEventListener('toggle',()=>{if(legacy.open)run(()=>attempt(legacyRefresh));});
    handoffHistory.append(list,detail);
    async function open(item){
      const data=await call('mail-get',{draft_id:item.draft_id});list.hidden=true;detail.hidden=false;
      // Sending is deliberately offered only after an explicit preview of the saved content.
      renderMailItem(opened,data.item,refresh,{send:true});focusPage(detail);
    }
    async function refresh() {
      rows.replaceChildren(node('p','메일 기록을 조회하고 있습니다.','hint'));navigation.replaceChildren();
      try{
        const data=await call('mail-history',{offset,source_kind:'',review_id:''});renderMailList(rows,data,open);
        navigation.append(node('p',`전체 ${data.total}건 · ${data.total?Math.min(offset+1,data.total):0}–${Math.min(offset+data.items.length,data.total)}건 표시`));
        const controls=[];
        if(offset>0)controls.push(button('이전 메일 기록',()=>{offset=Math.max(0,offset-data.limit);return refresh();}));
        if(offset+data.limit<data.total)controls.push(button('다음 메일 기록',()=>{offset+=data.limit;return refresh();}));
        navigation.append(actions(...controls));
      }catch(error){rows.replaceChildren(node('p','메일 기록을 조회하지 못했습니다. 새로고침으로 다시 확인하세요.','message error'));throw error;}
    }
    loadHandoffHistory=refresh;
  }
  mountProducts();mountRequests();mountHandoffHistory();await attempt(search);
}

'use strict';

// B supplies interpretation and arithmetic; the common host owns identity,
// exact-version proposals, source preservation and separate human approval.
function fulfillmentField(parent, label, name, options = {}) {
  const wrap = node('label', label), input = node(options.rows ? 'textarea' : 'input');
  input.name = name;
  input.required = options.required !== false;
  if (options.rows) input.rows = options.rows;
  else input.type = options.type || 'text';
  if (input.type === 'number') { input.min = String(options.min ?? 0); input.step = '1'; }
  input.placeholder = options.placeholder || '';
  wrap.append(input); parent.append(wrap); return input;
}
function fulfillmentSelect(parent, label, name, rows) {
  const wrap=node('label',label), select=node('select'); select.name=name; select.required=true;
  if (!rows.length) { const empty=node('option','승인된 기록 없음');empty.value='';select.append(empty); }
  for(const [value,text] of rows) { const option=node('option',text); option.value=value; select.append(option); }
  wrap.append(select);parent.append(wrap);return select;
}
function fulfillmentResult(container, response) {
  container.replaceChildren();const data=response.data, state=data.forecast || data;
  container.append(node('h3','조회 결과 · 승인 데이터 v'+response.data_version));
  container.append(node('p','명시한 기준 시각의 가상 운영 기록입니다. 조건부 예상과 실제 도착·생산 완료 사실은 구분합니다.','hint'));
  const meanings={complete:'입력 범위 확인',needs_confirmation:'추가 확인 필요',estimated:'조건부 예상',on_time:'조건상 납기 이내',late:'조건상 납기 초과'};
  const names={status:'확인 상태',as_of:'기준 시각',quantity:'약속 수량',allocated_quantity:'배정 수량',
    remaining_quantity:'미도착 잔량',unallocated_quantity:'미배정 수량',on_time_quantity:'조건상 납기 내 수량',
    shortfall_quantity:'조건상 부족 수량',estimated_full_arrival_at:'조건부 전체 도착 예상',
    estimated_completion_at:'조건부 완료 예상',deadline_status:'납기 조건 판정',target_quantity:'목표 생산 수량',
    work_completed_quantity:'기록된 정상 완료 누계',latest_good_quantity:'최근 정상 누계',good_rate_per_hour:'관측 구간의 시간당 정상 수량'};
  if(state.assessment_scope==='requested_production_work') names.remaining_quantity='남은 정상 생산 수량';
  const rows=Object.entries(names).filter(([key])=>key in state).map(([key,label])=>[label,meanings[state[key]] || (state[key] ?? '미확인')]);
  if(data.delivery_round) rows.unshift(['고객',data.contract.customer_name],['제품 구성',data.configuration.id],
    ['약속 수량',data.delivery_round.quantity],['도착 기준 납기',data.delivery_round.due_at],['도착지',data.delivery_round.destination]);
  if(rows.length)container.append(simpleTable(['항목','값'],rows));
  if(state.states) {
    const labels={delivered:'도착 확인',in_transit:'운송 중',warehouse:'창고 배정',in_progress:'미완료 생산',ready:'생산 준비',waiting:'생산 대기',production_completed:'생산 완료 기록 · 재고 입고 미확정'};
    container.append(simpleTable(['수량 구분','개'],Object.entries(state.states).map(([key,value])=>[labels[key]||key,value])));
  }
  if(state.unknowns?.length)container.append(simpleTable(['추가 확인 대상','항목','사유'],state.unknowns.map(row=>[row.node_id,row.field,row.code])));
  if(data.customer_reply)container.append(jsonDetails('검토용 고객 답변 초안 · 발송하지 않음',data.customer_reply));
  if(response.evidence?.length)container.append(evidenceButton(response.evidence,response.data_version));
  container.append(jsonDetails('조회 조건·계산·경고 전체',response));
}

async function mountFulfillment(container) {
  const selection=businessSelection, version=selection.version;
  const active=()=>container.isConnected&&businessSelection===selection&&businessSelection.version===version;
  const loading=node('p','계약 예제를 불러오는 중…','hint');container.append(loading);
  let response;
  try {response=await api('/api/fulfillment/query',{action:'demo_contract',arguments:{version}});}
  catch(error) {if(active())loading.replaceWith(node('p',error.message,'message error'));return;}
  if(!active())return;
  loading.remove();
  if(!response.ok&&response.errors?.some(issue=>['INVALID_INPUT','ROUTE_NOT_FOUND'].includes(issue.code))) {
    return mountRegisteredFulfillment(container);
  }
  try {
    must(response);
    if(response.data_version!==version)throw new Error('조회 기준 버전이 다릅니다. 현황을 다시 조회하세요.');
    const catalog=response.data;
    const contracts=catalog.contracts;
    if(!Array.isArray(contracts)||!contracts.length||contracts.some(row=>!row.contract||!row.input_sha256)) {
      throw new Error('계약 예제 응답을 확인할 수 없습니다. 서버 설정을 확인한 뒤 다시 조회하세요.');
    }
    mountDemoFulfillment(container,catalog,version,active);
  } catch(error) {container.append(node('p',error.message,'message error'));}
}

function mountDemoFulfillment(container,catalog,version,active) {
  const boundary=node('div');boundary.setAttribute('id','b-compact-fulfillment');boundary.setAttribute('data-records-open','false');
  const sheet=node('link');sheet.setAttribute('rel','stylesheet');sheet.setAttribute('href','/static/b-fulfillment.css');boundary.append(sheet);
  container.append(boundary);
  const states=catalog.contracts.map(input=>({input,step:0,reviewed:false,transportHours:null,transportMinutes:null,transportDirty:false,responseBasis:null,
    intake:null,forecast:null,comparison:null,response:null,error:'',loading:false,open:{}}));
  let selected=states[0];
  const card=node('section',null,'card'), cards=node('div',null,'form-grid work-links');
  card.append(node('h2','계약'),node('p','가상·미등록 계약의 발주 메일을 검토합니다. 납품 전망은 별도 운영 자료를 사용합니다.','hint'),cards);boundary.append(card);
  const workflow=node('section',null,'card'), navigation=node('div',null,'quick-actions'), panel=node('div',null,'record-detail-panel');
  workflow.append(navigation,panel);boundary.append(workflow);
  const steps=['계약 확인','납품 전망','대응 준비'];
  const value=x=>x===undefined||x===null||x===''?'미확인':String(x);
  const quantity=x=>x===undefined||x===null?'미확인':x+'개';
  const signed=(x,unit)=>x===undefined||x===null?'미확인':(x>0?'+':'')+x+unit;
  const duration=seconds=>{
    if(seconds===undefined||seconds===null)return '미확인';
    const total=Math.abs(seconds),hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),rest=total%60;
    return [hours?hours+'시간':'',minutes?minutes+'분':'',rest?rest+'초':''].filter(Boolean).join(' ')||'0분';
  };
  const timeChange=seconds=>seconds===undefined||seconds===null?'미확인':seconds===0?'변동 없음':
    duration(seconds)+(seconds>0?' 늦어짐':' 빨라짐');
  const time=x=>{
    if(!x)return '미확인';
    const date=new Date(x);
    return Number.isNaN(date.getTime())?String(x):new Intl.DateTimeFormat('ko-KR',{
      timeZone:'Asia/Seoul',year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'
    }).format(date)+' KST';
  };
  const names={id:'식별자',contract_number:'계약 번호',company:'우리 회사',customer_name:'고객사',contract_id:'계약',
    line_number:'계약 항목 번호',configuration_id:'제품 구성',quantity:'수량',unit:'단위',contract_item_id:'계약 항목',
    round_number:'납품 회차',round_quantity:'회차 약속 수량',product_name:'제품 모델',revision:'설계 개정',variant:'구성',
    due_at:'납품 기한',due_basis:'기한 기준',destination:'납품처',
    delivered:'납품 완료',in_transit:'운송 중',warehouse:'창고',production_completed:'생산 완료·미출고',
    in_progress:'생산 중',ready:'생산 준비',waiting:'생산 대기',
    target_quantity:'생산 목표',latest_good_quantity:'관측 양품 누계',remaining_quantity:'남은 양품 수량',
    good_rate_per_hour:'관측 양품 속도 · 개/시간',estimated_completion_at:'조건부 생산 완료 예상',observation_as_of:'생산 관측 시각',
    preparation_seconds:'준비 시간',transport_seconds:'운송 시간',dispatch_mode:'출고 방식'};
  function fieldValue(key,x) {
    if(key.endsWith('_at')||key==='observation_as_of')return time(x);
    if(key.endsWith('_seconds'))return duration(x);
    return ({arrival:'고객 도착',departure:'출발',piece:'개',all_at_once:'전량 일괄',split:'분할 출고',split_available:'준비 물량부터 분할 출고'})[x]||value(x);
  }
  function fields(target,data,keys) {
    target.append(simpleTable(['항목','내용'],keys.filter(key=>key in (data||{})).map(key=>[names[key]||key,fieldValue(key,data[key])])));
  }
  function detail(target,state,key,title) {
    const element=node('details');element.open=!!state.open[key];element.append(node('summary',title));
    element.addEventListener('toggle',()=>{state.open[key]=element.open;});target.append(element);return element;
  }
  function prose(target,text) {const p=node('p',value(text),'source-box');p.style.whiteSpace='pre-wrap';target.append(p);}
  function region(target,title,extra='') {
    const section=node('section',null,'b-region'+(extra?' '+extra:''));section.append(node('h4',title));target.append(section);return section;
  }
  function metrics(target,forecast) {
    const grid=node('div',null,'b-metrics');
    for(const [label,text] of [['기한 내 도착 예상',quantity(forecast.on_time_quantity)],
      ['부족 예상',quantity(forecast.shortfall_quantity)],['전량 도착 예상',time(forecast.estimated_full_arrival_at)]]) {
      const metric=node('div',null,'metric'),strong=node('strong',text);metric.append(node('small',label),strong);grid.append(metric);
      if(label==='전량 도착 예상'&&forecast.estimated_full_arrival_at) {
        metric.className+=' b-arrival';
        const date=new Date(forecast.estimated_full_arrival_at);
        if(!Number.isNaN(date.getTime())) {
          strong.textContent=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'numeric',day:'numeric'}).format(date);
          strong.append(node('span',new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date)+' KST'));
        }
      }
    }
    target.append(grid);
  }
  function button(target,label,action,disabled=false,primary=false) {
    const element=node('button',label,primary?'primary':'');element.type='button';element.disabled=disabled;
    element.addEventListener('click',action);target.append(element);return element;
  }
  function execute(state,action,key,trigger,extra={}) {
    if(state.loading||selected!==state||!active())return;
    run(async()=>{
      state.loading=true;state.error='';render();
      try {
        const response=must(await api('/api/fulfillment/query',{
          action,arguments:{version,input_sha256:state.input.input_sha256,...extra}
        }));
        if(response.data_version!==version)throw new Error('조회 기준 버전이 다릅니다. 현황을 다시 조회하세요.');
        if(!active()||selected!==state)return;
        state[key]=response;
        if(key==='forecast'){
          state.response=null;state.comparison=null;state.responseBasis=null;state.transportDirty=false;
          state.transportHours=null;state.transportMinutes=null;
        }
        if(key==='comparison'){
          state.responseBasis={minutes:response.data.comparison_input.transport_minutes,forecast:response.data.assessment.after.forecast};
          state.transportDirty=false;
        }
      } catch(error) {if(active()&&selected===state)state.error=error.message||'요청을 완료하지 못했습니다.';}
      finally {state.loading=false;if(active())render();}
    },trigger);
  }
  function actionButton(target,state,label,action,key,requiresForecast=false) {
    const configured=(state.input.model_ready??catalog.model_ready)===true&&(state.input.cost_ready??catalog.cost_ready)===true;
    const needsModel=['demo_intake','demo_response'].includes(action);
    const extra=action==='demo_response'&&state.responseBasis?{transport_minutes:state.responseBasis.minutes}:{};
    const element=button(target,label,()=>execute(state,action,key,element,extra),
      state.loading||(needsModel&&!configured)||(requiresForecast&&!state.forecast)||(action==='demo_response'&&state.transportDirty),true);
    return element;
  }
  function renderComparison(target,state) {
    const section=region(target,'운송 조건 변경','b-comparison');
    const current=state.forecast?.data.forecast.condition?.transport_seconds;
    section.append(node('p','현재 운송 시간 '+duration(current)+' · 변경할 시간을 입력해 납품 전망을 비교하세요.','hint'));
    const initial=(current??7200)+3600;
    if(state.transportHours===null){state.transportHours=String(Math.floor(initial/3600));state.transportMinutes=String(Math.floor(initial%3600/60));}
    const inputs=node('div',null,'b-transport-inputs');section.append(inputs);
    const hours=fulfillmentField(inputs,'시간','transport_hours',{type:'number',min:0}),
      minutes=fulfillmentField(inputs,'분','transport_minutes',{type:'number',min:0});
    hours.max='168';minutes.max='59';hours.value=state.transportHours;minutes.value=state.transportMinutes;
    hours.disabled=minutes.disabled=state.loading;
    const result=node('div',null,'b-comparison-output');result.setAttribute('aria-live','polite');
    const changed=()=>{
      state.transportHours=hours.value;state.transportMinutes=minutes.value;state.comparison=null;state.transportDirty=true;
      result.replaceChildren(node('p','입력이 바뀌었습니다. 「변경 영향 확인」을 다시 실행하세요.','hint'));
    };
    hours.addEventListener('input',changed);minutes.addEventListener('input',changed);
    const compare=button(inputs,'변경 영향 확인',()=>{
      const h=hours.value.trim(),m=minutes.value.trim(),total=Number(h)*60+Number(m);
      if(!/^\d+$/.test(h)||!/^\d+$/.test(m)||Number(m)>59||total>10080) {
        result.replaceChildren(node('p','시간은 0~168, 분은 0~59로 입력하세요. 합계는 168시간 이하여야 합니다.','message error'));return;
      }
      execute(state,'demo_compare','comparison',compare,{transport_minutes:total});
    },state.loading,true);
    section.append(node('p','입력 범위 0~168시간 · 비교가 완료된 조건으로 다음 대응 초안을 작성합니다. 원래 전망은 유지합니다.','hint'),result);
    if(state.transportDirty)result.append(node('p','입력이 바뀌었습니다. 「변경 영향 확인」을 다시 실행하세요.','hint'));
    if(!state.comparison)return;
    const data=state.comparison.data, assessment=data.assessment, before=assessment.before, after=assessment.after;
    const old=before.forecast, next=after.forecast, quantities=assessment.quantity_deltas, times=assessment.time_deltas;
    result.append(simpleTable(['항목','변경 전','변경 후','변화'],[
      ['운송 시간',duration(old.condition?.transport_seconds),duration(next.condition?.transport_seconds),duration(next.condition?.transport_seconds-old.condition?.transport_seconds)+(next.condition?.transport_seconds===old.condition?.transport_seconds?' · 동일':next.condition?.transport_seconds>old.condition?.transport_seconds?' 증가':' 감소')],
      ['기한 내 도착 예상',quantity(old.on_time_quantity),quantity(next.on_time_quantity),signed(quantities.on_time_quantity,'개')],
      ['부족 예상',quantity(old.shortfall_quantity),quantity(next.shortfall_quantity),signed(quantities.shortfall_quantity,'개')],
      ['전량 도착 예상',time(old.estimated_full_arrival_at),time(next.estimated_full_arrival_at),timeChange(times.estimated_full_arrival_seconds)],
      ['생산 완료 예상',time(before.production?.estimated_completion_at),time(after.production?.estimated_completion_at),timeChange(times.estimated_completion_seconds)],
      ['관측 양품 속도',value(before.production?.good_rate_per_hour),value(after.production?.good_rate_per_hour),signed(assessment.production_deltas.good_rate_per_hour,'개/시간')]
    ]));
    result.append(node('p','비교한 조건이 대응 준비의 작성 기준입니다. 기존 초안은 자동 교체하지 않습니다.','hint'),jsonDetails('비교 상세 근거',data));
  }
  function render() {
    if(!active())return;
    const state=selected,input=state.input,promise=input.contract;
    recordsToggle.disabled=state.loading;
    cards.replaceChildren();navigation.replaceChildren();panel.replaceChildren();
    for(const item of states) {
      const p=item.input.contract, title=p.contract_number||p.contract_id;
      const select=button(cards,'',()=>{selected=item;render();},state.loading,item===state);
      select.setAttribute('aria-pressed',String(item===state));
      select.append(node('strong',title),node('span',value(p.customer_name)+' · '+value(p.product_name)+' '+value(p.revision)+' / '+value(p.variant)),
        node('span',quantity(p.quantity)+' · '+time(p.due_at)),node('small','가상·미등록','hint'));
    }
    steps.forEach((label,index)=>{
      const select=button(navigation,(index+1)+'. '+label,()=>{state.step=index;state.error='';render();},state.loading,state.step===index);
      select.setAttribute('aria-pressed',String(state.step===index));
    });
    panel.setAttribute('aria-busy',String(state.loading));
    panel.append(node('h3',steps[state.step]));
    if(state.loading){const notice=node('p','처리 중…','message');notice.setAttribute('role','status');panel.append(notice);}
    if(state.error){const notice=node('p',state.error+' 기존 입력과 결과는 유지됩니다. 같은 버튼으로 다시 시도하세요.','message error');notice.setAttribute('role','alert');panel.append(notice);}
    if(state.step!==1&&(input.model_ready??catalog.model_ready)!==true)panel.append(node('p','모델 연결이 필요합니다. B 서버의 모델 설정을 완료한 뒤 화면을 다시 여세요.','hint'));
    else if(state.step!==1&&(input.cost_ready??catalog.cost_ready)!==true)panel.append(node('p','모델 비용 한도 설정이 필요합니다. B 서버의 비용 설정을 완료한 뒤 화면을 다시 여세요.','hint'));
    if(state.step===0) {
      panel.append(node('p','발주 메일의 최종 계약 조건을 해석하고 추출 항목을 원문과 대조합니다.','hint'));
      actionButton(panel,state,'계약 해석','demo_intake','intake');
      if(state.intake) {
        const data=state.intake.data;
        prose(panel,data.answer_ko);
        const extraction=data.extraction;
        if(extraction?.status==='ready') {
          const contract=extraction.nodes.find(row=>row.type==='Contract'),item=extraction.nodes.find(row=>row.type==='ContractItem'),
            round=extraction.nodes.find(row=>row.type==='DeliveryRound');
          const product=item?.configuration_id===promise.configuration_id?
            [promise.product_name,promise.revision,promise.variant].map(value).join(' / '):'구성 연결 확인 필요';
          panel.append(simpleTable(['추출 항목','내용'],[
            ['계약 번호',value(contract?.id?.replace(/^contract:demo:/,''))],['고객사',value(contract?.customer_name)],
            ['제품 · 개정 · 구성',product],['계약 수량',quantity(item?.quantity)],['회차 약속 수량',quantity(round?.quantity)],
            ['납품 기한',time(round?.due_at)+' · '+fieldValue('due_basis',round?.due_basis)],['납품처',value(round?.destination)]
          ]));
        }
        panel.append(jsonDetails('추출 상태·항목 상세',extraction));
        if(extraction?.issues?.length)panel.append(jsonDetails('추출 시 확인할 사항',extraction.issues));
        const review=node('label',null,'check'), check=node('input');check.type='checkbox';check.checked=state.reviewed;check.disabled=state.loading;
        check.addEventListener('change',()=>{state.reviewed=check.checked;});review.append(check,node('span','원문과 추출값을 대조했습니다 · 이 화면에서만 표시'));panel.append(review);
        panel.append(node('p','검토 표시는 저장·등록·승인을 수행하지 않습니다.','hint'));
      }
      const source=detail(panel,state,'source','계약 원문');
      prose(source,input.source_text.split('## 시연 상황')[0]);
      if(input.operations_source)source.append(jsonDetails('납품 전망에 사용하는 별도 운영 자료',input.operations_source));
      source.append(node('p',value(input.input_path),'hint'),node('p','SHA-256 · '+input.input_sha256,'hint'));
      return;
    }
    if(state.step===1) {
      panel.append(node('p','운영 관측과 준비·운송 가정으로 납품 가능성을 계산합니다. 실제 출고·도착을 보증하지 않습니다.','hint'));
      actionButton(panel,state,'납품 전망 확인','demo_forecast','forecast');
      if(state.forecast) {
        const data=state.forecast.data,forecast=data.forecast;
        metrics(panel,forecast);
        panel.append(node('p',({met:'조건상 기한 내 도착 예상',shortfall:'조건상 일부 부족 예상',needs_confirmation:'추가 확인 필요'})[forecast.deadline_status]||'조건부 예상','hint'));
        panel.append(node('p','기준 '+time(input.as_of)+' · 조회 '+time(input.queried_at),'hint'));
        const overview=node('div',null,'b-two-column');panel.append(overview);
        const status=region(overview,'물량 현황');
        status.append(simpleTable(['구분','수량'],Object.entries(data.fulfillment?.states||{}).map(([key,x])=>[names[key]||key,quantity(x)])));
        const production=region(overview,'생산 실적·완료 예상');
        fields(production,data.production,['target_quantity','latest_good_quantity','remaining_quantity','good_rate_per_hour','observation_as_of','estimated_completion_at']);
        production.append(node('p','생산 완료 예상은 재고 입고·출고·고객 도착 확인과 구분합니다.','hint'));
        renderComparison(panel,state);
        const condition=detail(panel,state,'condition','계산 조건·확인 사항·상세 근거');
        fields(condition,forecast.condition,['preparation_seconds','transport_seconds','dispatch_mode']);
        condition.append(jsonDetails('가정·경고·미확인',{
          assumptions:data.details?.assumptions,warnings:data.details?.warnings,unknowns:forecast.unknowns,confirmations:data.confirmations
        }));
        condition.append(node('p','계약 해석의 저장·승인 결과가 아닌, 같은 계약 조건으로 준비된 별도 운영 자료의 전망입니다.','hint'));
        condition.append(jsonDetails('물량별 계산·전체 근거',data));
      }
      return;
    }
    panel.append(node('p','담당자 검토용 초안 · 미발송','hint'));
    const basis=state.responseBasis?.forecast||state.forecast?.data.forecast;
    if(basis) {
      panel.append(node('p','운송 시간 '+duration(basis.condition?.transport_seconds)+' 기준 · '+
        (state.responseBasis?'비교 완료 조건':'원래 조건'),'hint'));
      metrics(panel,basis);
    }
    if(state.transportDirty)panel.append(node('p','운송 시간 입력이 바뀌었습니다. 납품 전망에서 「변경 영향 확인」을 다시 실행한 뒤 초안을 작성하세요.','message'));
    const stale=state.response&&basis&&state.response.data.assessment.forecast.condition?.transport_seconds!==basis.condition?.transport_seconds;
    if(stale)panel.append(node('p','재작성 필요 · 아래 초안은 이전 운송 조건으로 작성되었습니다.','message'));
    actionButton(panel,state,'대응 초안 작성','demo_response','response',true);
    if(!state.forecast)panel.append(node('p','「납품 전망 확인」을 먼저 실행하세요.','hint'));
    if(state.response) {
      const data=state.response.data,mails=node('div',null,'b-two-column');
      panel.append(node('p','아래 초안: 운송 시간 '+duration(data.assessment.forecast.condition?.transport_seconds)+' 기준','hint'),mails);
      for(const [key,label] of [['logistics','물류 요청'],['customer','고객 회신']]) {
        const mail=region(mails,label+' 메일');
        const body=node('div',null,'b-mail');prose(body,data.sections?.[key]);mail.append(body);
      }
      const other=detail(panel,state,'responseChecks','요약·추가 확인');
      other.append(node('h4','요약'));prose(other,data.sections?.summary);other.append(node('h4','추가 확인'));prose(other,data.sections?.checks);
      detail(panel,state,'fullAnswer','전체 모델 답변').append(node('pre',value(data.answer_ko)));
    }
  }
  let recordsOpen=false;
  const recordsToggle=button(boundary,'등록 기록 보기',()=>{
    recordsOpen=!recordsOpen;boundary.setAttribute('data-records-open',String(recordsOpen));
    recordsToggle.textContent=recordsOpen?'등록 기록 접기':'등록 기록 보기';
    recordsToggle.setAttribute('aria-expanded',String(recordsOpen));
  });
  recordsToggle.setAttribute('aria-expanded','false');recordsToggle.setAttribute('aria-controls','business-records');
  render();
}

async function mountRegisteredFulfillment(container) {
  const version=businessSelection.version;
  container.append(node('p','승인 데이터 v'+version+' · 등록된 계약의 납품 회차를 확인합니다. 가상 운영 기록이며 예상 결과는 실제 출고·도착을 보증하지 않습니다.','hint'));
  const kinds=['DeliveryRound','ProductionWork','FinishedGoodsStock','Allocation','Shipment','FulfillmentSnapshot',
    'Contract','ContractItem','Configuration','ProductRevision','ProductModel'];
  const values=await Promise.all(kinds.map(async kind=>{
    const response=await tool('lookup',{kind,version});
    if(response.data_version!==version)throw new Error('B 업무 기준 버전이 다릅니다. 현황을 다시 조회하세요.');
    return [kind,response.data];
  }));
  const records=Object.fromEntries(values), choices=kind=>records[kind].map(row=>[row.id,row.id]);
  const index=new Map(values.flatMap(([,rows])=>rows).map(row=>[row.id,row]));
  const contractCard=node('section',null,'card'), cards=node('div',null,'form-grid work-links');
  contractCard.append(node('h2','계약'),node('p','계약과 납품 회차를 선택하세요.','hint'),cards);container.append(contractCard);
  const workflow=node('section',null,'card'), navigation=node('div',null,'quick-actions'), panel=node('div',null,'record-detail-panel');
  workflow.append(navigation,panel);container.append(workflow);
  const states=new Map(), cardButtons=new Map();let selected=null;
  const steps=['계약 확인','납품 전망','대응 준비'];
  const text=value=>value===null||value===undefined||value===''?'미확인':String(value);
  const quantity=value=>value===null||value===undefined?'미확인':value+'개';
  const stateLabels={delivered:'납품 완료',in_transit:'운송 중',warehouse:'창고',production_completed:'생산 완료·미출고',
    in_progress:'생산 중',ready:'생산 준비',waiting:'생산 대기',mixed_production:'생산 완료분·진행분 혼합',
    not_delivered_by_deadline:'기한 내 도착 미확인'};
  const time=value=>{
    if(!value)return '미확인';
    const parsed=new Date(value);
    return Number.isNaN(parsed.getTime())?String(value):new Intl.DateTimeFormat('ko-KR',{
      timeZone:'Asia/Seoul',year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'
    }).format(parsed)+' KST';
  };
  function promiseFor(round) {
    const item=index.get(round.contract_item_id), contract=index.get(item?.contract_id), configuration=index.get(item?.configuration_id),
      revision=index.get(configuration?.revision_id), product=index.get(revision?.model_id);
    return {delivery_round:round,contract,contract_item:item,configuration,revision,product};
  }
  function summary(target,promise) {
    target.append(simpleTable(['항목','내용'],[
      ['계약',promise.contract?.contract_number||promise.contract?.id||'미확인'],
      ['고객사',text(promise.contract?.customer_name)],['제품',text(promise.product?.name)],
      ['개정 / 구성',text(promise.revision?.revision)+' / '+text(promise.configuration?.variant)],
      ['납품 회차',promise.delivery_round.id],['약속 수량',quantity(promise.delivery_round.quantity)],
      ['납품 기한',time(promise.delivery_round.due_at)+' · '+(promise.delivery_round.due_basis==='arrival'?'고객 도착 기준':'출발 기준')],
      ['납품처',text(promise.delivery_round.destination)]
    ]));
  }
  function details(target,label,response) {
    if(response.evidence?.length)target.append(evidenceButton(response.evidence,version));
    target.append(jsonDetails(label,response));
  }
  async function query(action,state,extra={}) {
    const arguments_={delivery_round_id:state.round.id,version,...extra};
    if(action!=='delivery_round')arguments_.as_of=state.asOf;
    const response=must(await api('/api/fulfillment/query',{action,arguments:arguments_}));
    if(response.data_version!==version)throw new Error('조회 기준 버전이 다릅니다. 최신 승인 버전을 다시 조회하세요.');
    return response;
  }
  function execute(button,state,action) {
    run(async()=>{
      state.error='';state.loading=true;render();
      try {await action();}
      catch(error){state.error=(error.message||'조회에 실패했습니다.')+' 입력을 확인한 뒤 다시 실행하세요.';throw error;}
      finally {state.loading=false;if(container.isConnected&&businessSelection?.view==='fulfillment'&&businessSelection.version===version)render();}
    },button);
  }
  function metrics(target,forecast) {
    const grid=node('div',null,'form-grid');
    for(const [label,value] of [['기한 내 도착 예상',quantity(forecast.on_time_quantity)],
      ['부족 예상',quantity(forecast.shortfall_quantity)],['전량 도착 예상',time(forecast.estimated_full_arrival_at)]]) {
      const metric=node('div',null,'metric');metric.append(node('small',label),node('strong',value));grid.append(metric);
    }
    target.append(grid);
  }
  function productionDetails(target,response) {
    const data=response.data;
    target.append(simpleTable(['항목','내용'],[
      ['목표 수량',quantity(data.target_quantity)],['관측 양품 누계',quantity(data.latest_good_quantity)],
      ['남은 양품 수량',quantity(data.remaining_quantity)],['관측 기준',time(data.observation_as_of)],
      ['최근 양품 속도',data.good_rate_per_hour==null?'미확인':data.good_rate_per_hour+'개/시간'],
      ['조건부 완료 예상',time(data.estimated_completion_at)],['목표 달성 관측 시각',time(data.target_observed_at)]
    ]));
    if(data.observations?.length) {
      target.append(node('h4','계산에 사용된 관측 실적'),simpleTable(['관측 시각','생산 누계','양품 누계','불량 누계'],
        data.observations.map(row=>[time(row.observed_at),quantity(row.produced_quantity),quantity(row.good_quantity),quantity(row.defective_quantity)])));
    }
    target.append(node('p','최근 관측 구간의 추세가 계속되고 양품을 이 회차에 사용할 수 있다는 조건의 예상입니다. 생산 완료는 출고·고객 도착 확인과 구분합니다.','hint'));
    if(data.unknowns?.length)target.append(node('p','완료 예상을 위한 관측 또는 배정 확인이 부족합니다. 아래 근거 상세에서 확인할 항목을 살펴보세요.','message'));
    details(target,'생산 실적·계산 근거',response);
  }
  function quantityDetails(target,state) {
    const section=node('details'), items=state.status?.data.items||[];
    const forecasts=new Map((state.forecast?.data.breakdown||[]).map(row=>[row.allocation_id,row]));
    section.append(node('summary','물량별 상세'));
    for(const item of items) {
      const forecast=forecasts.get(item.allocation.id), part=node('details');
      part.append(node('summary',(stateLabels[item.state]||'상태 미확인')+' · '+quantity(item.quantity)+' · '+item.source.id));
      part.append(simpleTable(['항목','내용'],[
        ['배정',item.allocation.id],['물량 출처',item.source.id],['배정 수량',quantity(item.quantity)],
        ['현재 상태',item.classification_confirmed?(stateLabels[item.state]||'미확인'):'추가 확인 필요'],
        ['출처 관측 시각',time(item.observed_at)],['현재 확보 수량',quantity(forecast?.known_available_quantity)],
        ['기한 내 도착 예상',quantity(forecast?.on_time_quantity)],['실제 출발',time(item.shipment?.departed_at)],
        ['실제 도착',time(forecast?.actual_arrival_at||item.shipment?.arrived_at)],
        ['조건부 도착 예상',forecast?.actual_arrival_at?'실제 도착 확인됨':time(forecast?.estimated_arrival_at)]
      ]));
      if(item.state_quantities)part.append(simpleTable(['생산 물량 구분','수량'],Object.entries(item.state_quantities).map(([key,value])=>[stateLabels[key]||key,quantity(value)])));
      const evidence=[...new Set([...(item.evidence||[]),...(forecast?.evidence||[])])];
      if(evidence.length)part.append(evidenceButton(evidence,version));
      section.append(part);
    }
    if(!items.length)section.append(node('p','확인할 배정 물량이 없습니다. 입력·근거 상세를 확인하세요.','hint'));
    section.append(node('p','현재 확보 수량에는 이미 도착했거나 운송 중인 물량이 포함될 수 있습니다. 새 출고 검토 수량은 대응 준비에서 구분합니다.','hint'));
    target.append(section);
  }
  function comparisonDetails(target,state) {
    const section=node('details'), form=node('form'), fields=node('div',null,'form-grid'), output=node('div');
    section.open=!!state.comparison||!!state.comparisonOpen;
    section.addEventListener('toggle',()=>{state.comparisonOpen=section.open;});
    section.append(node('summary','변경 전후 비교'),node('p','같은 계약·배정·출하 계획을 유지한 두 승인 버전을 비교합니다. 각 버전의 기준 시각을 입력하세요. 운송 조건을 임의로 변경하거나 결과를 저장하지 않습니다.','hint'),form,output);
    const inputs={};
    for(const [key,label,options,value] of [
      ['before_version','변경 전 승인 버전',{type:'number'},''],['after_version','변경 후 승인 버전',{type:'number'},String(version)],
      ['before_as_of','변경 전 기준 시각 · 시간대 포함',{},state.asOf],['after_as_of','변경 후 기준 시각 · 시간대 포함',{},state.asOf]
    ]) {
      const input=fulfillmentField(fields,label,'comparison_'+key,options);input.value=state.comparisonInput?.[key]??value;
      if(options.type==='number')input.max=String(version);
      input.disabled=state.loading;inputs[key]=input;
      input.addEventListener('input',()=>{
        state.comparisonInput=Object.fromEntries(Object.entries(inputs).map(([name,field])=>[name,field.value.trim()]));
        state.comparison=null;state.comparisonOpen=true;output.replaceChildren();
      });
    }
    form.append(fields);
    const button=node('button','변경 영향 확인');button.type='submit';button.disabled=state.loading;form.append(button);
    form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;
      state.comparisonInput=Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value.trim()]));
      const args={delivery_round_id:state.round.id,...state.comparisonInput};
      args.before_version=Number(args.before_version);args.after_version=Number(args.after_version);
      state.comparisonOpen=true;execute(button,state,async()=>{
        state.comparison=null;
        const response=await api('/api/fulfillment/query',{action:'compare_response_plan',arguments:args});
        if(!response.ok){
          const code=response.errors?.[0]?.code;
          const reasons={INCOMPARABLE_INPUTS:'계약·물량 출처·배정·출하 계획이 달라 비교할 수 없습니다.',
            NOT_FOUND:'선택한 버전에 이 납품 회차가 없습니다.',INVALID_VERSION:'비교 전·후의 유효한 승인 버전을 확인하세요.',
            INVALID_TIME:'각 버전에 기록된 기준 시각을 시간대와 함께 입력하세요. 같은 버전은 같은 시각으로만 비교할 수 있습니다.'};
          if(reasons[code])throw new Error(reasons[code]);must(response);
        }
        if(response.data_version!==args.after_version)throw new Error('변경 후 조회 버전이 일치하지 않습니다. 다시 확인하세요.');
        state.comparison=response;
      });
    });
    if(state.comparison) {
      const data=state.comparison.data, before=data.before, after=data.after;
      const old=before.response_plan.forecast, next=after.response_plan.forecast;
      const signed=(value,unit)=>value==null?'미확인':(value>0?'+':'')+value+unit;
      output.append(node('p',data.comparison_status==='partially_unknown'?'일부 결과는 입력 부족으로 산정할 수 없습니다. 미확인은 0이 아닙니다.':'동일한 계산 규칙으로 두 승인 버전을 비교했습니다.','hint'));
      output.append(simpleTable(['항목','변경 전 · v'+before.data_version,'변경 후 · v'+after.data_version,'변화'],[
        ['기준 시각',time(before.as_of),time(after.as_of),'—'],
        ['기한 내 도착 예상',quantity(old.on_time_quantity),quantity(next.on_time_quantity),signed(data.quantity_deltas.on_time_quantity,'개')],
        ['부족 예상',quantity(old.shortfall_quantity),quantity(next.shortfall_quantity),signed(data.quantity_deltas.shortfall_quantity,'개')],
        ['전량 도착 예상',time(old.estimated_full_arrival_at),time(next.estimated_full_arrival_at),signed(data.time_deltas.estimated_full_arrival_seconds,'초')],
        ['생산 완료 예상',time(before.production?.estimated_completion_at),time(after.production?.estimated_completion_at),signed(data.time_deltas.estimated_completion_seconds,'초')]
      ]));
      const inputChanges=node('details');inputChanges.append(node('summary','변경된 입력'));
      const labels={transport_seconds:'운송 시간(초)',preparation_seconds:'준비 시간(초)',observed_at:'관측 시각',as_of:'기준 시각',
        completed_quantity:'양품 완료 누계',produced_quantity:'생산 누계',good_quantity:'양품 누계',defective_quantity:'불량 누계'};
      const changes=data.changed_inputs.filter(row=>labels[row.field]);
      if(changes.length)inputChanges.append(simpleTable(['대상','항목','변경 전','변경 후'],changes.map(row=>[row.node_id,labels[row.field],row.before_present?text(row.before):'기록 없음',row.after_present?text(row.after):'기록 없음'])));
      else inputChanges.append(node('p','주요 수량·시간 입력의 변화가 없습니다. 전체 근거에서 다른 변경 항목을 확인할 수 있습니다.','hint'));
      output.append(inputChanges);
      const responses=node('details');responses.append(node('summary','대응 내용 비교'));
      responses.append(simpleTable(['항목','변경 전','변경 후'],['new_dispatch_quantity','currently_available_quantity','future_quantity'].map((key,i)=>[
        ['새 출고 검토','현재 확보','미래 생산 예상'][i],quantity(before.response_plan.dispatch_request[key]),quantity(after.response_plan.dispatch_request[key])])));
      for(const [label,side] of [['변경 전',before],['변경 후',after]]) {
        responses.append(node('h4',label+' 고객 회신'),node('p',side.response_plan.customer_reply,'source-box'));
        const evidence=data.evidence_by_side[label==='변경 전'?'before':'after'];
        if(evidence.length)responses.append(evidenceButton(evidence,side.data_version));
      }
      output.append(responses,node('p','이 비교는 기존 대응 초안을 교체하지 않습니다. 변경 후 버전의 대응 준비에서 다시 작성하세요.','hint'),jsonDetails('비교 조건·전체 근거',state.comparison));
    }
    target.append(section);
  }
  function render() {
    navigation.replaceChildren();panel.replaceChildren();
    if(!selected){panel.append(node('p','이 버전에 등록된 납품 회차가 없습니다.','empty-row'));return;}
    const state=states.get(selected);
    for(const [id,button] of cardButtons){button.setAttribute('aria-pressed',String(id===selected));button.classList.toggle('primary',id===selected);}
    steps.forEach((label,step)=>{
      const button=node('button',(step+1)+'. '+label,step===state.step?'primary':'');button.type='button';
      button.setAttribute('aria-pressed',String(step===state.step));button.disabled=state.loading;
      button.addEventListener('click',()=>{state.step=step;state.error='';render();});navigation.append(button);
    });
    panel.setAttribute('aria-busy',String(state.loading));
    if(state.loading)panel.append(node('p','조회 중…','message'));
    if(state.error){const error=node('p',state.error,'message error');error.setAttribute('role','alert');panel.append(error);}
    panel.append(node('h3',steps[state.step]));
    if(state.step===0) {
      summary(panel,state.promise?.data||promiseFor(state.round));
      const button=node('button','계약 정보 확인','primary');button.type='button';button.disabled=state.loading;
      button.addEventListener('click',()=>execute(button,state,async()=>{state.promise=null;state.promise=await query('delivery_round',state);}));panel.append(button);
      if(state.promise)details(panel,'계약 정보·근거 상세',state.promise);
      panel.append(node('p','등록된 계약과 근거를 확인합니다. 계약 확인은 실제 납품 이행 확인과 구분합니다.','hint'));
      const next=node('button','납품 전망으로');next.type='button';next.disabled=state.loading;
      next.addEventListener('click',()=>{state.step=1;render();});panel.append(next);
      return;
    }
    if(state.step===1) {
      const form=node('form'), fields=node('div',null,'form-grid'), output=node('div');form.append(fields);panel.append(form,output);
      const asOf=fulfillmentField(fields,'기준 시각 · 시간대 포함','forecast_as_of',{placeholder:'2026-10-27T10:00:00+09:00'});asOf.value=state.asOf;
      asOf.disabled=state.loading;
      function changeTime(){
        if(state.asOf===asOf.value.trim())return;
        state.asOf=asOf.value.trim();state.stale=state.stale||!!state.response;
        state.status=null;state.forecast=null;state.production=null;state.response=null;state.error='';
        output.replaceChildren(node('p','기준 시각이 변경되었습니다. 납품 전망을 다시 확인하세요.','hint'));
      }
      asOf.addEventListener('change',changeTime);
      const button=node('button','납품 전망 확인','primary');button.type='submit';button.disabled=state.loading;form.append(button);
      form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity())return;changeTime();execute(button,state,async()=>{
        state.stale=state.stale||!!state.response;state.response=null;state.status=null;state.forecast=null;state.production=null;
        state.status=await query('fulfillment_status',state);
        state.forecast=await query('delivery_forecast',state);
      });});
      if(state.forecast) {
        const forecast=state.forecast.data;
        metrics(output,forecast);
        const label={met:'조건상 기한 내 도착 예상',shortfall:'조건상 일부 부족 예상',needs_confirmation:'추가 확인 필요'};
        output.append(node('p',(label[forecast.deadline_status]||'조건부 예상')+' · 기준 '+time(forecast.as_of),'hint'));
      }
      if(state.status) {
        const labels={delivered:'납품 완료',in_transit:'운송 중',warehouse:'창고',production_completed:'생산 완료·미출고',in_progress:'생산 중',ready:'생산 준비',waiting:'생산 대기'};
        output.append(node('h3','물량 현황'));
        if(state.status.data.states)output.append(simpleTable(['물량 구분','수량'],Object.entries(state.status.data.states).map(([key,value])=>[labels[key]||key,quantity(value)])));
        output.append(node('p','생산 완료·미출고는 창고 입고나 고객 도착을 뜻하지 않습니다.','hint'));
        details(output,'물량 현황·확인 사항',state.status);
      }
      if(state.forecast) {
        const forecast=state.forecast.data;
        quantityDetails(output,state);
        const condition=node('details');condition.append(node('summary','운송 조건'));
        if(forecast.condition)condition.append(simpleTable(['항목','값'],[
          ['준비 시간',text(forecast.condition.preparation_seconds)+'초'],['운송 시간',text(forecast.condition.transport_seconds)+'초'],
          ['출고 방식',forecast.condition.dispatch_mode==='all_at_once'?'전량 일괄':'분할 출고']
        ]));
        condition.append(node('p','이 전망은 등록된 준비·운송 조건을 기준으로 합니다.','hint'));output.append(condition);
        details(output,'계산 조건·근거·미확인 사항',state.forecast);
        const next=node('button','대응 준비로');next.type='button';next.disabled=state.loading;
        next.addEventListener('click',()=>{state.step=2;render();});output.append(next);
      }
      const workIds=new Set(records.Allocation.filter(row=>row.delivery_round_id===state.round.id&&row.production_work_id).map(row=>row.production_work_id));
      const works=records.ProductionWork.filter(row=>workIds.has(row.id));
      const production=node('details');production.append(node('summary','생산 실적 상세'));output.append(production);
      if(works.length) {
        const productionForm=node('form'), productionOutput=node('div');production.append(productionForm,productionOutput);
        const work=fulfillmentSelect(productionForm,'생산 작업','forecast_work',works.map(row=>[row.id,row.id]));
        if(state.workId&&workIds.has(state.workId))work.value=state.workId;
        work.disabled=state.loading;
        work.addEventListener('change',()=>{state.workId=work.value;state.production=null;productionOutput.replaceChildren();});
        const confirm=node('button','생산 전망 확인');confirm.type='submit';confirm.disabled=state.loading;productionForm.append(confirm);
        productionForm.addEventListener('submit',event=>{event.preventDefault();if(!state.asOf){state.error='기준 시각을 입력하세요.';render();return;}
          state.workId=work.value;execute(confirm,state,async()=>{state.production=null;state.production=await query('production_completion',state,{production_work_id:state.workId});});});
        if(state.production){production.open=true;productionDetails(productionOutput,state.production);}
      } else production.append(node('p','이 회차에 연결된 생산 작업이 없습니다.','hint'));
      comparisonDetails(output,state);
      return;
    }
    panel.append(node('p','물류 담당자와 고객 응대 담당자가 검토할 초안을 준비합니다. 자동 발송·출고는 수행하지 않습니다.','hint'));
    if(state.stale)panel.append(node('p','기준 조건 또는 전망을 다시 확인했습니다. 대응 초안을 새로 작성하세요.','message'));
    const button=node('button','대응 초안 작성','primary');button.type='button';button.disabled=state.loading||!state.forecast;
    button.addEventListener('click',()=>execute(button,state,async()=>{state.response=null;state.response=await query('response_plan',state);state.stale=false;}));panel.append(button);
    if(!state.forecast)panel.append(node('p','납품 전망에서 기준 시각을 확인하고 「납품 전망 확인」을 먼저 실행하세요.','hint'));
    if(state.response) {
      const data=state.response.data, tabs=node('div',null,'quick-actions');panel.append(tabs);
      for(const [key,label] of [['logistics','물류 요청'],['customer','고객 회신']]) {
        const tab=node('button',label,state.tab===key?'primary':'');tab.type='button';tab.disabled=state.loading;
        tab.setAttribute('aria-pressed',String(state.tab===key));tab.addEventListener('click',()=>{state.tab=key;render();});tabs.append(tab);
      }
      panel.append(node('p','담당자 검토용 · 미발송','hint'));
      if(state.tab==='logistics') {
        const request=data.dispatch_request;
        panel.append(simpleTable(['항목','수량'],[
          ['이미 도착 · 새 출고 제외',quantity(request.delivered_quantity)],['운송 중 · 새 출고 제외',quantity(request.in_transit_quantity)],
          ['새 출고 검토',quantity(request.new_dispatch_quantity)],['현재 확보',quantity(request.currently_available_quantity)],
          ['미래 생산 예상',quantity(request.future_quantity)],['기한 내 도착 검토',quantity(request.on_time_new_dispatch_quantity)],
          ['기한 후 도착 검토',quantity(request.later_new_dispatch_quantity)]
        ]),node('p',request.detail));
        panel.append(simpleTable(['요청 항목','내용'],[['고객사',text(request.customer_name)],['납품처',text(request.destination)],['납품 기한',time(request.due_at)]]));
      } else panel.append(node('p',data.customer_reply,'source-box'));
      if(data.actions?.length) {
        const labels={REVIEW_HISTORY:'이행 이력 검토',CONFIRM_INPUTS:'입력·근거 확인',REVIEW_DISPATCH:'출고 검토',
          MONITOR_IN_TRANSIT:'운송 중 물량 확인',AGREE_FOLLOWUP:'후속 일정 협의 검토',CONFIRM_SUPPLY:'미배정 물량 확인'};
        panel.append(node('h4','검토할 일'),simpleTable(['검토 항목','관련 수량','내용'],data.actions.map(row=>[
          labels[row.code]||'추가 검토',row.quantity==null?'—':quantity(row.quantity),row.detail])));
      }
      if(data.confirmations?.length) {
        const labels={carrier:'운송사',vehicle:'차량',detailed_address:'상세 주소',contact:'연락처',request_number:'신청 번호',
          CUSTOMER_AGREEMENT_REQUIRED:'고객 일정 합의',DISPATCH_CAPACITY_REQUIRED:'출고 능력·작업 시간'};
        const confirmations=node('details');confirmations.open=true;confirmations.append(node('summary','대응 확인 사항'),simpleTable(['확인 항목','내용'],
          data.confirmations.map(row=>[labels[row.field]||labels[row.code]||'전망 입력·근거',row.detail])));panel.append(confirmations);
      }
      details(panel,'대응 준비·근거 상세',state.response);
    }
  }
  for(const round of records.DeliveryRound) {
    const promise=promiseFor(round), button=node('button');button.type='button';
    button.append(node('strong',promise.contract?.contract_number||promise.contract?.id||round.id),
      node('span',text(promise.contract?.customer_name)+' · '+text(promise.product?.name)),
      node('span',quantity(round.quantity)+' · '+time(round.due_at)),node('span','납품 회차 · '+round.id));
    cards.append(button);cardButtons.set(round.id,button);
    states.set(round.id,{round,step:0,asOf:records.FulfillmentSnapshot.find(row=>row.round_ids.includes(round.id))?.as_of||'',tab:'logistics',loading:false,error:'',stale:false});
    button.addEventListener('click',()=>{selected=round.id;render();});
  }
  selected=records.DeliveryRound[0]?.id||null;render();

  const editCard=node('details',null,'card'), form=node('form'), fields=node('div'), source=node('div'), feedback=node('div');
  editCard.append(node('summary','부분 생산·분할 출하 변경안 작성'),form,feedback);container.append(editCard);
  const operation=fulfillmentSelect(form,'등록할 업무','fulfillment_action',[
    ['production_progress','부분 생산 누계 기록'],['split_allocation','기존 미출하 배정을 분할'],['split_shipments','미배정 재고에서 출하 회차 생성']]);
  form.append(fields,source); businessSourceFields(source);
  const save=node('button','등록 초안 저장 · 별도 검토 필요','primary');save.type='submit';
  save.disabled=!context.identity.permissions.includes('draft');form.append(save);
  let collect;
  function buildForm() {
    fields.replaceChildren();const grid=node('div',null,'form-grid');fields.append(grid);
    if(operation.value==='production_progress') {
      const target=fulfillmentSelect(grid,'진행 중인 생산 작업','progress_work',choices('ProductionWork'));
      const detail=node('p',null,'hint');fields.append(detail);
      const observed=fulfillmentField(grid,'새 누계 관측 시각','progress_observed',{placeholder:'2026-10-27T11:00:00+09:00'});
      const produced=fulfillmentField(grid,'생산 누계 · 정상+불량','progress_produced',{type:'number'});
      const good=fulfillmentField(grid,'정상 완료 누계','progress_good',{type:'number'});
      const defective=fulfillmentField(grid,'제공된 불량 누계 · 원인 판정 아님','progress_defective',{type:'number'});
      const observationId='observation:web:'+crypto.randomUUID();
      const extendLabel=node('label',null,'check'),extend=node('input');extend.type='checkbox';extend.name='extend_snapshot';
      extendLabel.append(extend,node('span','선택 기준 시각도 갱신하고 완전성 조건을 새로 확인'));fields.append(extendLabel);
      const cutoff=node('div');cutoff.hidden=true;fields.append(cutoff);
      const snapshotAt=fulfillmentField(cutoff,'새 기준 시각 · 시간대 포함','progress_snapshot',{required:false,placeholder:'2026-10-27T11:00:00+09:00'});
      cutoff.append(node('p','아래 항목은 근거로 확인한 경우만 선택하세요. 선택하지 않으면 미확인으로 보존되어 전체 수량을 확정하지 않습니다.','hint'));
      const labels={allocations_complete:'회차별 배정 목록 완전',source_allocations_complete:'재고·작업의 전체 배정 목록 완전',allocations_disjoint:'배정 수량 간 중복 없음',stock_events_complete:'재고 입출고 기록 완전',stock_observations_before_departures:'재고 관측이 출발 이전',shipment_events_complete:'출하 기록 완전',production_state_complete:'생산 상태 기록 완전',material_disjoint:'재고와 생산 물량 중복 없음',no_reallocation:'미기록 재배정 없음'};
      const checks={};for(const [key,label] of Object.entries(labels)){const wrap=node('label',null,'check'),input=node('input');input.type='checkbox';input.name=key;checks[key]=input;wrap.append(input,node('span',label));cutoff.append(wrap);}
      extend.addEventListener('change',()=>{cutoff.hidden=!extend.checked;snapshotAt.required=extend.checked;});
      function describe(){const selected=records.ProductionWork.find(row=>row.id===target.value),snap=records.FulfillmentSnapshot.find(row=>row.id===selected?.snapshot_id);detail.textContent=selected?'기존 정상 누계 '+selected.completed_quantity+' / 목표 '+selected.target_quantity+' · 관측 '+selected.observed_at+' · 기준 '+snap?.as_of:'승인된 생산 작업이 없습니다.';}
      target.addEventListener('change',describe);describe();
      collect=()=>{const value={production_work_id:target.value,observation_id:observationId,observed_at:observed.value.trim(),produced_quantity:Number(produced.value),good_quantity:Number(good.value),defective_quantity:Number(defective.value)};if(extend.checked){value.snapshot_as_of=snapshotAt.value.trim();value.confirmations=Object.fromEntries(Object.entries(checks).map(([key,input])=>[key,input.checked]));}return value;};
      return;
    }
    const splitting=operation.value==='split_allocation';
    const eligible=records.Allocation.filter(row=>row.stock_id&&!records.Shipment.some(ship=>ship.allocation_id===row.id));
    const target=fulfillmentSelect(grid,splitting?'출하 기록이 없는 기존 배정':'완제품 재고',splitting?'split_allocation':'split_stock',splitting?eligible.map(row=>[row.id,row.id+' · '+row.quantity+'개']):choices('FinishedGoodsStock'));
    const targetRound=splitting?null:fulfillmentSelect(grid,'납품 회차','split_round',choices('DeliveryRound'));
    fields.append(node('p',splitting?'회차 수량 합계는 기존 배정량과 같아야 합니다. 첫 회차는 기존 배정 ID를 유지합니다.':'이미 다른 배정에 사용된 수량은 재사용할 수 없습니다. 서버가 전체 회차 합계를 검증합니다.','hint'));
    fields.append(node('p','출발·도착 시각은 확인된 경우만 입력하세요. 빈 값은 미확인이며 실제 출발·도착으로 처리하지 않습니다. 각 회차 도착은 회차 전체 수량 기준입니다.','hint'));
    const parts=node('div');fields.append(parts);const partForms=[];
    function addPart(){
      if(partForms.length>=20)return;
      const row=node('fieldset'),legend=node('legend','출하 회차 '+(partForms.length+1)),group=node('div',null,'form-grid');row.append(legend,group);parts.append(row);
      const index=partForms.length, aid=splitting&&index===0?target.value:'allocation:web:'+crypto.randomUUID(),sid='shipment:web:'+crypto.randomUUID();
      const qty=fulfillmentField(group,'회차 수량','part_quantity_'+index,{type:'number',min:1});
      const times={};for(const [key,label] of [['planned_departure_at','예정 출발'],['departed_at','확인된 실제 출발'],['expected_arrival_at','예상 도착'],['arrived_at','확인된 실제 도착']])times[key]=fulfillmentField(group,label+' · 선택',key+'_'+index,{required:false,placeholder:'2026-10-27T10:00:00+09:00'});
      const part={aid,sid,qty,times};partForms.push(part);
      row.append(actionButton('이 회차 제거',async()=>{if(splitting&&partForms[0]===part)throw new Error('첫 회차는 기존 배정 ID를 보존하므로 제거할 수 없습니다.');partForms.splice(partForms.indexOf(part),1);row.remove();}));
    }
    fields.append(actionButton('출하 회차 추가',async()=>addPart()));addPart();addPart();
    target.addEventListener('change',()=>{if(splitting&&partForms[0])partForms[0].aid=target.value;});
    collect=()=>({...(splitting?{allocation_id:target.value}:{stock_id:target.value,delivery_round_id:targetRound.value}),parts:partForms.map(part=>({allocation_id:part.aid,shipment_id:part.sid,quantity:Number(part.qty.value),...Object.fromEntries(Object.entries(part.times).filter(([,input])=>input.value.trim()).map(([key,input])=>[key,input.value.trim()]))}))});
  }
  operation.addEventListener('change',buildForm);buildForm();
  form.addEventListener('submit',event=>{event.preventDefault();run(async()=>{
    const body={domain:'fulfillment',action:operation.value,payload:collect(),base_version:version,...businessSourceValue(form)};
    body.request_key=requestKey('fulfillment-draft',body);
    showBusinessDraft(must(await api('/api/business/draft',body)),feedback);
  },save);});
}

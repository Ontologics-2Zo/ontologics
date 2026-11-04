'use strict';
// Shared form/display helpers. The server owns identity, versions and approval.
function businessField(form, name, label, {type='text', value='', required=true, placeholder='', options=null}={}) {
  const wrapper=node('label',label), input=node(options ? 'select' : type==='textarea' ? 'textarea' : 'input');
  input.name=name;
  if (!options && type!=='textarea') input.type=type;
  if(options) for(const item of options) {const option=node('option',item.label);option.value=item.value;input.append(option);}
  if(type==='textarea') input.rows=3;
  if(type==='number') {input.min='0'; input.step='1';}
  if(type==='checkbox') input.checked=value===true; else input.value=value;
  input.required=required;input.placeholder=placeholder;wrapper.append(input);form.append(wrapper);return input;
}
function businessSourceFields(form) {
  const area=node('fieldset');area.append(node('legend','원본 기록 근거 · 시연용 가상 자료'));
  businessField(area,'source_text','접수 원문 또는 기록 내용',{type:'textarea',placeholder:'신고 원문, 생산 보고 또는 출하 기록을 그대로 적으세요.'});
  businessField(area,'authored_at','근거 작성 시각 · 시간대 포함',{placeholder:'2026-11-04T10:00:00+09:00'});
  businessField(area,'notice','가상 자료의 목적·범위',{placeholder:'예: 시연용 가상 출하 기록. 실제 납품 성과 아님.'});
  businessField(area,'synthetic_confirmed','실제 고객·시험·출하 사실이 아닌 시연용 가상 기록임을 확인합니다.',{type:'checkbox'});
  form.append(area);
}
function businessSourceValue(form) {
  const values={};for(const name of ['source_text','authored_at','notice']) values[name]=name==='source_text' ? form.elements[name].value : form.elements[name].value.trim();
  values.synthetic_confirmed=form.elements.synthetic_confirmed.checked;return values;
}
function businessSubmit(form,label,handler) {
  const button=node('button',label,'primary');button.type='submit';form.append(button);
  form.addEventListener('submit',event=>{event.preventDefault();if(form.reportValidity())run(handler,button);});return button;
}
function showBusinessDraft(response, output) {
  const target=output || $('business-feedback');if(!target)return;
  target.replaceChildren();const data=response.data;
  target.append(node('p','등록·변경 초안 저장 · 아직 확정 반영되지 않았습니다.','message'),
    node('p',data.draft_id+' · 초안 v'+data.draft_version+' · 기준 데이터 v'+response.data_version));
  if(data.validation) target.append(jsonDetails('등록 조건 검증 결과',data.validation));
  if(data.registration)target.append(node('p',data.registration.notice));
  target.append(actionButton('등록안 검토 화면으로',async()=>{selectView('review');await loadDraft({id:data.draft_id,version:data.draft_version});}));
}
async function submitBusiness(form,domain,action,payload,output) {
  const body={domain,action,payload,base_version:businessSelection.version,...businessSourceValue(form)};
  body.request_key=requestKey('business-draft',body);
  const response=must(await api('/api/business/draft',body));showBusinessDraft(response,output);return response;
}

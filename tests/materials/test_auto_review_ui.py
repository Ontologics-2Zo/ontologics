"""Run real UI handlers with a minimal DOM; no browser or network side effects."""
from pathlib import Path
import subprocess
import unittest

class AutoReviewUITests(unittest.TestCase):
    def test_pdf_retry_partial_failure_and_concurrent_review(self):
        source=Path(__file__).resolve().parents[2]/'ontologics/materials/library_static/app.js'
        harness=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
const nodes=[];
class E{constructor(tag,text){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};nodes.push(this);}append(...xs){this.children.push(...xs);}replaceChildren(...xs){this.children=xs;}setAttribute(){}addEventListener(){}scrollIntoView(){}insertBefore(n){this.children.push(n);}querySelector(){return null;}querySelectorAll(){return [];}after(){}get classList(){return {toggle(){}};}}
const ids={};const $=id=>ids[id]||(ids[id]=new E('div'));
let apiImpl,opens=0,refreshes=0;
const ctx={console,crypto:require('crypto').webcrypto,$,el:(t,s)=>new E(t,s),date:()=>'',notice:()=>{},action:fn=>fn(),open:async()=>{opens++;},api:(...a)=>apiImpl(...a),document:{querySelector:s=>$(s)},showMaterials:async()=>{ctx.materialOpened=true;}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('const labels='),source.indexOf('function renderStorage'))+source.slice(source.indexOf('// Automatic previews')),ctx);
(async()=>{
const f={state:'supported',value:100,claims:[]};
const row={id:'i',result_sha256:'h',status:'needs_review',checks:[],result:{original:{text:'PDF original'},items:[{fields:{ordered_quantity:f}}]}};
const original={id:'c',title:'test',text:null,review:row};
apiImpl=async()=>({});
ctx.renderReview(new E('details'),original,async()=>{refreshes++;});
assert(nodes.some(n=>n.tag==='pre'&&n.textContent==='PDF original'));
await nodes.find(n=>n.tag==='button'&&n.textContent==='다시 해석하기').onclick();
assert.equal(refreshes,1);assert.equal(opens,0);
const fields={ordered_quantity:{state:'supported',value:100,source:'source_validated'}};
const item={fields,candidates:[{exact:true,configuration_id:'cfg',name:'P',comparison:{},sources:[]}],readiness:{ready:true,blocking_questions:[],other_unknowns:[]},basis_hash:'b'};
const data={title:'test',interpretation_id:'i',result_sha256:'h',data_version:1,review_id:null,items:[item]};
apiImpl=async(op)=>{if(op==='planning-preview')return data;if(op==='get')return original;if(op==='materials-preview')throw Error('temporary read failure');throw Error(op);};
await ctx.showPlanning('c');
assert(nodes.some(n=>n.tag==='button'&&n.textContent==='자동 검토 새로고침'));
const button=nodes.filter(n=>n.tag==='button'&&n.textContent==='내용 확인·제품 연결 후 생산 수량 검토 →').at(-1);
assert(button,'preview failure must retain continue');
let saved=false,connected=false;
apiImpl=async(op)=>{if(op==='planning-preview')return data;if(op==='review'){saved=true;return {};}
if(op==='get')return {...original,review:{...row,checks:[{id:'other-review',decisions:{'0:ordered_quantity':{state:'corrected',value:'999',reason:'concurrent'}}}]}};
if(op==='connect'){connected=true;return {};}throw Error(op);};
await assert.rejects(button.onclick(),/다른 확인 내용/);assert(saved);assert(!connected);
apiImpl=async()=>{throw Error('history unavailable');};
await ctx.renderCustomer(new E('section'),{id:'c',buyer:{state:'linked',customer:{id:'customer-a',name:'Acme'},link:{method:'exact_name'},provenance:{evidence:[]}}},()=>{});
assert(nodes.some(n=>n.textContent==='고객의 다른 계약을 불러오지 못했습니다. 현재 계약 검토는 계속할 수 있습니다.'));
console.log('PDF/retry/preview/concurrent-review/customer-history handlers passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result=subprocess.run(['node','-e',harness,str(source)],text=True,capture_output=True)
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)

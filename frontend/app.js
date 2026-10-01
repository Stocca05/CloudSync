const $ = (id) => document.getElementById(id);
const state = {user: null, remotes: [], jobs: [], providers: {}, page: 'overview'};
let timer, toastTimer, appleTimer, appleJob, appleChallenge, editingRemote=null, googlePolling=false;
const speedHistory = Array(30).fill(0);
const operationLabels={copy:'Copia',move:'Spostamento',sync:'Sincronizzazione speculare',bisync:'Sincronizzazione 2 vie (bidirezionale)',list:'Esplorazione',configure:'Accesso iCloud',mkdir:'Nuova cartella',delete:'Eliminazione'};
const labels = {queued:'In coda', running:'In corso', completed:'Completato', failed:'Errore', cancelled:'Annullato'};
function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; }
function button(text, action, cls='secondary') { const b=el('button',cls,text); b.type='button'; b.onclick=event=>guard(()=>action(event)); return b; }
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
async function guard(action){try{await action();}catch(e){toast(e.message);}}
async function api(path, options={}){
 const res=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','X-CloudSync-Request':'1',...options.headers}});
 if(!res.ok){let data;try{data=await res.json();}catch{data={detail:'Servizio non raggiungibile'};}
 if(res.status===401&&state.user){state.user=null;clearInterval(timer);$('shell').hidden=true;$('auth').hidden=false;}
 throw new Error(typeof data.detail==='string'?data.detail:'Controlla i campi inseriti');}return res.json();
}
const post=(path,data={})=>api(path,{method:'POST',body:JSON.stringify(data)});
function bytes(value){const n=Number(value)||0;if(n<1024)return Math.round(n)+' B';const i=Math.min(4,Math.floor(Math.log(n)/Math.log(1024)));return (n/1024**i).toFixed(1)+' '+['B','KiB','MiB','GiB','TiB'][i];}
function remoteName(id){return state.remotes.find(r=>r.id===id)?.name||'Collegamento';}
function empty(target,title,description){const node=$('empty-template').content.cloneNode(true);node.querySelector('h3').textContent=title;node.querySelector('p').textContent=description;target.replaceChildren(node);}
async function enter(){state.user=await api('/me');$('auth').hidden=true;$('shell').hidden=false;$('welcome').textContent='Ciao, '+state.user.username;$('avatar').textContent=state.user.username[0].toUpperCase();$('admin-nav').hidden=!state.user.admin;state.providers=await api('/providers');state.google=await api('/google/status');await loadRemotes();await refresh();clearInterval(timer);timer=setInterval(()=>guard(refresh),4000);}
$('login-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;$('auth-error').textContent='';try{const data=Object.fromEntries(new FormData(event.target));await post('/auth/login',data);event.target.reset();await enter();}catch(e){$('auth-error').textContent=e.message;}finally{b.disabled=false;}};
$('register').onclick=async()=>{const form=$('login-form');if(!form.reportValidity())return;try{const data=Object.fromEntries(new FormData(form));await post('/auth/register',data);await post('/auth/login',data);form.reset();await enter();}catch(e){$('auth-error').textContent=e.message;}};
$('logout').onclick=()=>guard(async()=>{await post('/auth/logout');clearInterval(timer);state.user=null;$('shell').hidden=true;$('auth').hidden=false;});
async function page(name){state.page=name;document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!=='page-'+name);document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('selected',b.dataset.page===name));if(name==='admin')await loadAdmin();if(name==='schedules')await loadSchedules();}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>guard(()=>page(b.dataset.page)));
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>guard(()=>page(b.dataset.go)));
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
async function refresh(){if(!state.user)return;state.jobs=await api('/jobs');renderJobs();if(state.page==='admin')await loadAdmin(false);if(state.page==='schedules')await loadSchedules();}
function renderJobs(){const jobs=state.jobs;updateChart(jobs.filter(j=>j.status==='running').reduce((sum,j)=>sum+(j.stats.speed||0),0)); $('metric-active').textContent=jobs.filter(j=>j.status==='running').length;$('metric-queued').textContent=jobs.filter(j=>j.status==='queued').length;$('metric-done').textContent=jobs.filter(j=>j.status==='completed').length;$('metric-speed').textContent=bytes(jobs.filter(j=>j.status==='running').reduce((s,j)=>s+(j.stats.speed||0),0))+'/s';const root=$('jobs');root.replaceChildren();if(!jobs.length)return empty(root,'Il tuo prossimo trasferimento parte da qui','Collega due cloud e scegli cosa copiare. Al resto pensa il server.');
 for(const j of jobs){const row=el('article','job');const isBi=j.operation==='bisync';row.append(el('div','job-icon',isBi?'⇄':(j.operation==='sync'?'➔':(j.operation==='move'?'→':'→'))));const info=el('div');info.append(el('h3','',['copy','move','sync','bisync'].includes(j.operation)?remoteName(j.source_id)+' '+(isBi?'⇄':'➔')+' '+remoteName(j.destination_id):(operationLabels[j.operation]||j.operation)+' · '+remoteName(j.source_id)));info.append(el('p','',(j.source_path||'/')+' '+(isBi?'⇄':'➔')+' '+(j.destination_path||'/')));let text=new Date(j.created*1000).toLocaleString('it-IT')+' · '+(operationLabels[j.operation]||j.operation);if(j.status==='running')text+=' · '+bytes(j.stats.bytes)+' / '+bytes(j.stats.totalBytes)+' · '+bytes(j.stats.speed)+'/s';if(j.node_id)text+=' · '+j.node_id;info.append(el('p','',text));if(j.error)info.append(el('p','error',j.error));if(j.status==='running'){const p=el('progress');p.max=100;p.value=j.stats.totalBytes?Math.min(100,j.stats.bytes/j.stats.totalBytes*100):0;p.setAttribute('aria-label','Avanzamento trasferimento');info.append(p);}row.append(info);const actions=el('div','job-actions');actions.append(el('span','badge '+j.status,j.cancel_requested&&j.status==='running'?'Annullamento…':labels[j.status]));if(j.operation==='configure'&&j.status==='running')actions.append(button('Completa accesso',()=>watchApple(j.id)));if(['queued','running'].includes(j.status))actions.append(button('Annulla',async()=>{await post('/jobs/'+j.id+'/cancel');await refresh();},'quiet'));if(['failed','cancelled'].includes(j.status))actions.append(button('Riprova',async()=>{await post('/jobs/'+j.id+'/retry');await refresh();}));row.append(actions);root.append(row);}}
async function loadRemotes(){state.remotes=await api('/remotes');for(const name of ['source','destination']){const select=$(name+'-remote'),old=select.value;select.replaceChildren(new Option('Scegli un collegamento',''));state.remotes.forEach(r=>select.add(new Option(r.name,r.id)));if(state.remotes.some(r=>r.id===old))select.value=old;}const root=$('remotes');root.replaceChildren();if(!state.remotes.length)return empty(root,'Unisci i tuoi spazi','Aggiungi il primo collegamento cloud per iniziare.');for(const r of state.remotes){const card=el('article','remote-card');card.append(el('div','remote-icon','◇'),el('h3','',r.name),el('p','muted',state.providers[r.provider]?.label||r.provider));if(r.provider==='iclouddrive')card.append(button('Collega / rinnova Apple',()=>startApple(r.id)),button('Aggiorna credenziali',()=>{openRemote();editingRemote=r.id;$('remote-form').elements.name.value=r.name;$('provider').value='iclouddrive';$('provider').disabled=true;providerFields();}));card.append(button('Esplora',async()=>{$('source-remote').value=r.id;await page('explorer');await browse('source');}),button('Rimuovi',async()=>{if(confirm('Rimuovere il collegamento '+r.name+'?')){await api('/remotes/'+r.id,{method:'DELETE'});await loadRemotes();}},'quiet'));root.append(card);}}
const fieldLabels={provider:'Provider S3 (AWS, Minio, Other…)',access_key_id:'Access key',secret_access_key:'Secret key',region:'Regione',endpoint:'Endpoint HTTPS (facoltativo per AWS)',host:'Host pubblico',port:'Porta',user:'Utente',pass:'Password',url:'URL HTTPS',vendor:'Vendor (other, nextcloud, owncloud)',token:'Token JSON Rclone',client_id:'Client ID (facoltativo)',client_secret:'Client secret (facoltativo)',apple_id:'Apple ID (email)',password:'Password del tuo account Apple',root_folder_id:'ID cartella radice (facoltativo)'};
function providerFields(){const type=$('provider').value,schema=state.providers[type],root=$('provider-fields');root.replaceChildren();$('remote-submit').textContent=type==='drive'?'Accedi con Google':'Salva collegamento';$('remote-submit').disabled=type==='drive'&&!state.google.configured;$('provider-help').textContent=type==='iclouddrive'?'Usa la password del tuo account Apple, non una password specifica per app. Il passaggio successivo chiederà il codice 2FA ricevuto sul tuo dispositivo.':type==='drive'?(state.google.configured?(state.google.method==='rclone'?'Accedi con il client Google di Rclone. L’assistente CloudSync deve essere attivo sul computer con cui navighi: aprirà Google e collegherà l’account automaticamente. L’assistente va avviato su ciascun computer che usi per collegare Google.':'Scegli il tuo account Google e autorizza CloudSync. Non servono file, ID o token.'):'L’amministratore deve attivare una sola volta l’accesso Google per questo sito. Poi potrai scegliere il tuo account senza file o token.'):type==='sftp'?'Il nodo deve conoscere la chiave host SFTP in known_hosts. Sono accettati host pubblici.':'Inserisci le credenziali del servizio. La connessione sarà verificata aprendo una cartella.';if(type==='drive'&&state.google.method==='rclone'){const link=el('a','','Scarica assistente Google');link.href='/api/google/helper';link.download='CloudSync-Google.py';root.append(link,el('p','muted','Su un nuovo computer: installa Python 3 e Rclone, poi esegui python3 CloudSync-Google.py e lascia l’assistente aperto. Torna qui e premi Accedi con Google.'));}for(const field of (type==='drive'?[]:schema.fields)){const label=el('label','',fieldLabels[field]||field),input=el(field==='token'?'textarea':'input');input.name=field;input.required=schema.required.includes(field);if(['pass','password','secret_access_key','client_secret'].includes(field))input.type='password';if(field==='provider')input.value='AWS';if(field==='vendor')input.value='other';if(field==='port')input.value='22';label.append(input);root.append(label);}}
function openRemote(){editingRemote=null;$('provider').disabled=false;$('remote-form').reset();$('remote-error').textContent='';$('provider').replaceChildren();Object.entries(state.providers).forEach(([id,p])=>$('provider').add(new Option(p.label,id)));providerFields();$('remote-dialog').showModal();}$('add-remote').onclick=openRemote;$('provider').onchange=providerFields;
$('remote-form').onsubmit=async e=>{e.preventDefault();if(googlePolling)return;const googleWindow=$('provider').value==='drive'?window.open('about:blank','cloudsync-google'):null;e.submitter.disabled=true;try{const config={};$('provider-fields').querySelectorAll('input,textarea').forEach(i=>{if(i.value)config[i.name]=i.value;});const provider=$('provider').value;if(provider==='drive'){const auth=await post('/google/start',{name:e.target.elements.name.value});if(googleWindow){googleWindow.opener=null;googleWindow.location=auth.url;}else{throw new Error('Consenti l’apertura della finestra Google nel browser e riprova.');}if(auth.method==='rclone')await waitGoogle(auth.ticket);return;}const values={name:e.target.elements.name.value,provider,config};const remote=editingRemote?await api('/remotes/'+editingRemote,{method:'PATCH',body:JSON.stringify(values)}):await post('/remotes',values);$('remote-dialog').close();e.target.reset();await loadRemotes();if(provider==='iclouddrive')await startApple(remote.id);else toast('Collegamento salvato. Apri una cartella per verificarlo.');}catch(err){$('remote-error').textContent=err.message;if(googleWindow&&!googleWindow.closed)googleWindow.close();}finally{googlePolling=false;e.submitter.disabled=false;}};
async function waitGoogle(ticket){
 googlePolling=true;$('remote-error').textContent='Completa l’accesso nella nuova finestra. CloudSync attende il salvataggio dell’autorizzazione.';
 const reasons={port_busy:'Un altro accesso Rclone sta usando la porta locale. Completalo o chiudilo e riprova.',shared_client:'Google ha rifiutato il client condiviso di Rclone. Serve un client Google configurato dall’amministratore.',upload:'Autorizzazione ottenuta, ma il server non l’ha salvata. Verifica la connessione e riprova.',authorization:'Accesso Google annullato o non completato.',timeout:'Tempo scaduto. Avvia un nuovo accesso.'};
 for(let i=0;i<300;i++){
  await new Promise(r=>setTimeout(r,2000));
  if(!state.user||!$('remote-dialog').open)return;
  const result=await post('/google/progress',{ticket});
  if(result.status==='connected'){$('remote-dialog').close();await loadRemotes();await page('remotes');toast('Google Drive collegato e salvato.');return;}
  if(result.status==='failed')throw new Error(reasons[result.reason]||reasons.authorization);
  if(result.status==='expired')throw new Error(reasons.timeout);
 }
 throw new Error(reasons.timeout);
}
async function browse(side){const id=$(side+'-remote').value,path=$(side+'-path').value;if(!id)throw new Error('Scegli un collegamento');const root=$(side+'-files');root.replaceChildren(el('p','muted','Lettura in coda… Il worker aprirà la cartella.'));const job=await post('/jobs',{operation:'list',source_id:id,source_path:path});let data;for(let i=0;i<90;i++){await new Promise(r=>setTimeout(r,1500));data=await api('/jobs/'+job.id);if(['completed','failed','cancelled'].includes(data.status))break;}root.replaceChildren();if(data.status!=='completed'){root.append(el('p','error',data.error||'La lettura non è ancora terminata. Riprova tra poco.'));return;}if(path){root.append(button('↑ Cartella superiore',()=>{$(side+'-path').value=path.split('/').slice(0,-1).join('/');return browse(side);},'quiet'));}
for(const item of data.result.items||[]){
  const container = el('div','file-entry');
  
  const row = button((item.IsDir?'▱ ':'· ')+item.Name,()=>{const p=[path,item.Path].filter(Boolean).join('/');$(side+'-path').value=p;if(item.IsDir)return browse(side);if(side==='source'){$('is-file').checked=true;$('destination-path').value=[$('destination-path').value,item.Name].filter(Boolean).join('/');}},'');
  row.append(el('small','',item.IsDir?'Cartella':bytes(item.Size)));

  
  const delBtn = button('✕', (e) => {
    e.stopPropagation();
    return handleDelete(side, [path, item.Path].filter(Boolean).join('/'), item.IsDir);
  }, 'quiet');
  delBtn.title = 'Elimina';
  delBtn.classList.add('delete-file');
  delBtn.setAttribute('aria-label','Elimina '+item.Name);
  
  container.append(row, delBtn);
  root.append(container);
}
if(!data.result.items?.length)root.append(el('p','muted','Questa cartella è vuota.'));if(data.result.truncated)root.append(el('p','notice','Mostrati i primi 1.000 elementi.'));}
$('browse-source').onclick=()=>guard(()=>browse('source'));$('browse-destination').onclick=()=>guard(()=>browse('destination'));
function transferSpec(){const op=$('operation').value;return {source_id:$('source-remote').value,destination_id:$('destination-remote').value,source_path:$('source-path').value,destination_path:$('destination-path').value,operation:op,is_file:['sync','bisync'].includes(op)?false:$('is-file').checked,priority:Number($('priority').value)};}
$('operation').onchange=()=>{const isSync=['sync','bisync'].includes($('operation').value);$('is-file').disabled=isSync;if(isSync)$('is-file').checked=false;};
$('transfer-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const data=transferSpec();if(!data.source_id||!data.destination_id)throw new Error('Scegli sorgente e destinazione');if(data.operation==='move'){if(!confirm('Spostare i file? Gli originali trasferiti con successo saranno rimossi dalla sorgente.'))return;data.confirm_move=true;}await post('/jobs',data);toast('Trasferimento in coda. Puoi chiudere il browser.');await page('overview');await refresh();});};
$('schedule-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const job=transferSpec();if(!job.source_id||!job.destination_id)throw new Error('Scegli sorgente e destinazione');if(job.operation==='move')job.operation='sync';const intervalSec=Number($('schedule-interval').value||60);await post('/schedules',{job,interval_seconds:intervalSec});toast('Sincronizzazione continua sempre attiva salvata');await page('schedules');});};
async function loadSchedules(){const data=await api('/schedules'),root=$('schedules');root.replaceChildren();if(!data.length)return empty(root,'Nessuna sincronizzazione attiva','Configura un processo continuo o periodico dalla pagina Esplora e trasferisci.');for(const s of data){const card=el('article','job');const isBi=s.template?.operation==='bisync';card.append(el('div','job-icon',isBi?'⇄':(s.template?.operation==='sync'?'➔':'◷')));const text=el('div');text.append(el('h3','',remoteName(s.template?.source_id)+' '+(isBi?'⇄':'➔')+' '+remoteName(s.template?.destination_id)),el('p','',(s.template?.source_path||'/')+' '+(isBi?'⇄':'➔')+' '+(s.template?.destination_path||'/')));const freq=s.interval_seconds<60?`Continuo ogni ${s.interval_seconds}s`:(s.interval_seconds===60?'Continuo ogni minuto':`Ogni ${s.interval_seconds/60} minuti`);const opName=operationLabels[s.template?.operation]||s.template?.operation||'Sincronizzazione';const nextText=s.enabled?`Prossimo controllo: ${new Date(s.next_run*1000).toLocaleTimeString('it-IT')}`:'In pausa';text.append(el('p','',`${opName} · ${freq} · ${nextText}`));card.append(text);const actions=el('div','job-actions');actions.append(el('span','badge '+(s.enabled?'running':'cancelled'),s.enabled?'Attivo':'In pausa'));actions.append(button('Sincronizza ora',async()=>{await post('/schedules/'+s.id+'/run');toast('Sincronizzazione avviata immediatamente.');await refresh();await page('overview');}));actions.append(button(s.enabled?'Pausa':'Riprendi',async()=>{await post('/schedules/'+s.id+'/toggle');toast(s.enabled?'Sincronizzazione messa in pausa.':'Sincronizzazione riattivata.');await loadSchedules();},'secondary'));actions.append(button('Elimina',async()=>{if(confirm('Eliminare questa sincronizzazione continua?')){await api('/schedules/'+s.id,{method:'DELETE'});toast('Sincronizzazione eliminata.');await loadSchedules();}},'quiet'));card.append(actions);root.append(card);}}
function numberField(text,value,min,max){const label=el('label','',text),input=el('input');input.type='number';input.value=value;input.min=min;if(max)input.max=max;input.required=true;label.append(input);return [label,input];}
async function loadAdmin(full=true){if(!state.user?.admin)return;const data=await api('/admin/cluster');state.clusterNodes=data.nodes;if(full){$('global-bandwidth').value=data.global_bps/1048576;$('max-active').value=data.max_active_jobs;}const nodes=$('nodes');nodes.replaceChildren();for(const n of data.nodes){const card=el('article','remote-card');card.append(el('div','remote-icon','⌘'),el('h3','',n.id),el('p','',!n.enabled?'Revocato':Date.now()/1000-n.last_seen<20?'Online':'Non connesso'),el('p','muted',`${n.slots} lavori · ${bytes(n.bandwidth_bps)}/s`));if(n.enabled)card.append(button('Revoca accesso',async()=>{if(confirm('Revocare il nodo? I lavori saranno interrotti e riassegnati.')){await api('/admin/nodes/'+n.id,{method:'DELETE'});await loadAdmin();}},'quiet'));nodes.append(card);}await loadAdminJobs();if(!full)return;const users=$('users');users.replaceChildren();for(const u of data.users){const form=el('form','panel user-row');form.append(el('h3','',u.username+(u.admin?' · Admin':'')));const [weightLabel,weight]=numberField('Peso banda',u.weight,1,10),[slotsLabel,slots]=numberField('Lavori massimi',u.max_jobs,1,16),[bandLabel,band]=numberField('MiB/s (0 = quota)',u.bandwidth_bps/1048576,0);form.append(weightLabel,slotsLabel,bandLabel);const enabled=el('input');enabled.type='checkbox';enabled.checked=u.enabled;const lab=el('label','checkbox','Attivo');lab.append(enabled);form.append(lab);const save=el('button','secondary','Salva');form.append(save);form.onsubmit=e=>{e.preventDefault();guard(async()=>{await api('/admin/users/'+u.id,{method:'PATCH',body:JSON.stringify({weight:Number(weight.value),max_jobs:Number(slots.value),bandwidth_bps:Math.round(Number(band.value)*1048576),enabled:enabled.checked})});toast('Priorità aggiornate');});};users.append(form);}}
$('cluster-form').onsubmit=e=>{e.preventDefault();guard(async()=>{await api('/admin/cluster',{method:'PATCH',body:JSON.stringify({global_bps:Math.round(Number($('global-bandwidth').value)*1048576),max_active_jobs:Number($('max-active').value)})});toast('Limiti del cluster aggiornati');});};
$('add-node').onclick=()=>{$('node-form').reset();$('node-token').hidden=true;document.querySelector('#node-token textarea').value='';$('node-dialog').showModal();};
$('node-form').onsubmit=e=>{e.preventDefault();guard(async()=>{const f=e.target.elements;const data=await post('/admin/nodes',{name:f.name.value,slots:Number(f.slots.value),bandwidth_bps:Math.round(Number(f.bandwidth.value)*1048576)});$('node-token').hidden=false;document.querySelector('#node-token textarea').value=data.token;await loadAdmin();});};
let adminJobOffset=0;
async function loadAdminJobs(){
 const data=await api('/admin/jobs?offset='+adminJobOffset+'&status='+encodeURIComponent($('admin-job-filter').value)),root=$('admin-jobs');
 if(root.contains(document.activeElement))return;
 root.replaceChildren();
 if(!data.items.length)empty(root,'Nessun processo','Non ci sono lavori per questo filtro.');
 for(const j of data.items){
  const row=el('article','job'),info=el('div');
  info.append(el('h3','',j.username+' · '+(operationLabels[j.operation]||j.operation)),el('p','muted',j.id),el('p','',labels[j.status]+' · Nodo: '+(j.node_id||'in attesa')),el('p','',bytes(j.stats.bytes)+' trasferiti · '+bytes(j.stats.speed)+'/s · Quota '+bytes(j.assigned_bps)+'/s'));
  if(j.error)info.append(el('p','error',j.error));
  const actions=el('div','job-actions');
  if(['queued','running'].includes(j.status)){
   const select=el('select');select.setAttribute('aria-label','Priorità processo '+j.id);
   ['Bassa','Normale','Alta'].forEach((label,i)=>select.add(new Option(label,String(i))));select.value=String(j.priority);
   const nodeSelect=el('select');nodeSelect.setAttribute('aria-label','Sposta nodo per '+j.id);
   nodeSelect.add(new Option('Auto (qualsiasi nodo)', ''));
   (state.clusterNodes||[]).filter(n=>n.enabled).forEach(n=>{
     const isCur = j.node_id === n.id;
     nodeSelect.add(new Option('Nodo: '+n.id+(isCur?' (in uso)':''), n.id));
   });
   if(j.node_id) nodeSelect.value = j.node_id;
   actions.append(
     select,
     button('Priorità',async()=>{await api('/admin/jobs/'+j.id,{method:'PATCH',body:JSON.stringify({priority:Number(select.value)})});toast('Priorità aggiornata.');}),
     nodeSelect,
     button('Sposta nodo ⇄',async()=>{
       const target = nodeSelect.value || null;
       if(j.status === 'running' && target === j.node_id){
         toast('Il lavoro è già in esecuzione su '+target);
         return;
       }
       await post('/admin/jobs/'+j.id+'/reassign', {node_id: target});
       toast('Lavoro migrato a '+(target?'nodo '+target:'coda automatica')+'. Il trasferimento riprende dal checkpoint senza ripartire da zero.');
       await loadAdminJobs();
       await refresh();
     },'secondary'),
     button('Interrompi',async()=>{if(confirm('Interrompere questo processo di '+j.username+'?')){await post('/admin/jobs/'+j.id+'/cancel');await loadAdminJobs();}},'quiet')
   );
  }
  row.append(info,actions);root.append(row);
 }
 $('admin-jobs-prev').disabled=adminJobOffset===0;$('admin-jobs-next').disabled=!data.has_more;$('admin-jobs-page').textContent='Pagina '+(adminJobOffset/100+1);
}
$('admin-job-filter').onchange=()=>{adminJobOffset=0;guard(loadAdminJobs);};
$('admin-jobs-prev').onclick=()=>{adminJobOffset=Math.max(0,adminJobOffset-100);guard(loadAdminJobs);};
$('admin-jobs-next').onclick=()=>{adminJobOffset+=100;guard(loadAdminJobs);};

async function startApple(remoteId){const job=await post('/remotes/'+remoteId+'/connect');watchApple(job.id);}
function watchApple(jobId){appleJob=jobId;appleChallenge=null;clearInterval(appleTimer);$('apple-status').textContent='In attesa del worker. La connessione usa Apple ID e 2FA.';$('apple-answer-label').hidden=true;$('apple-submit').hidden=true;$('apple-dialog').showModal();guard(pollApple);appleTimer=setInterval(()=>guard(pollApple),2000);}
async function pollApple(){if(!$('apple-dialog').open){clearInterval(appleTimer);return;}const job=await api('/jobs/'+appleJob);if(['completed','failed','cancelled'].includes(job.status)){clearInterval(appleTimer);$('apple-status').textContent=job.status==='completed'?'iCloud Drive collegato. Puoi esplorare e trasferire i tuoi file.':job.error||'Connessione annullata';$('apple-answer-label').hidden=true;$('apple-submit').hidden=true;$('apple-examples').replaceChildren();await refresh();return;}const c=job.result.challenge;if(c&&c.id!==appleChallenge){appleChallenge=c.id;$('apple-status').textContent=c.help;$('apple-answer').value='';$('apple-answer-label').hidden=false;$('apple-submit').hidden=false;$('apple-submit').disabled=false;$('apple-examples').replaceChildren();for(const example of c.examples||[]){$('apple-examples').append(button(example.label||example.value,()=>sendApple(example.value),'secondary'));}}else if(!c){appleChallenge=null;$('apple-status').textContent=job.status==='queued'?'Connessione in coda: attendo un worker disponibile…':'Verifica Apple in corso…';$('apple-answer-label').hidden=true;$('apple-submit').hidden=true;$('apple-examples').replaceChildren();}}
async function sendApple(answer){$('apple-submit').disabled=true;try{await post('/jobs/'+appleJob+'/answer',{challenge_id:appleChallenge,answer});$('apple-answer').value='';$('apple-status').textContent='Risposta inviata. Attendo Apple…';}catch(e){$('apple-submit').disabled=false;throw e;}}
$('apple-form').onsubmit=e=>{e.preventDefault();guard(()=>sendApple($('apple-answer').value));};
$('apple-cancel').onclick=()=>guard(async()=>{if(appleJob)await post('/jobs/'+appleJob+'/cancel');$('apple-dialog').close();clearInterval(appleTimer);});

function updateChart(speedBytes) {
 const canvas=$('speedChart');if(!canvas)return;
 speedHistory.push(Math.max(0,Number(speedBytes)||0));speedHistory.shift();
 const rect=canvas.getBoundingClientRect();if(!rect.width)return;
 const ratio=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);
 const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
 const w=rect.width,h=rect.height,pad=18,max=Math.max(1048576,...speedHistory);
 ctx.strokeStyle='#334155';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad,h-pad);ctx.lineTo(w-pad,h-pad);ctx.stroke();
 ctx.fillStyle='#94a3b8';ctx.font='12px system-ui';ctx.fillText(bytes(max)+'/s',pad,14);
 ctx.strokeStyle='#38bdf8';ctx.lineWidth=2;ctx.beginPath();
 speedHistory.forEach((speed,i)=>{const x=pad+i*(w-2*pad)/(speedHistory.length-1),y=h-pad-(speed/max)*(h-2*pad);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
}

async function handleMkdir(side) {
  const id = document.getElementById(side+'-remote').value;
  let currentPath = document.getElementById(side+'-path').value;
  if (!id) {
    toast('Scegli prima un collegamento.');
    return;
  }
  const folderName = prompt('Nome della nuova cartella (verrà creata in ' + (currentPath || 'radice') + '):');
  if (!folderName) return;
  const newPath = [currentPath, folderName].filter(Boolean).join('/');
  
  toast('Creazione cartella in corso...');
  const root = document.getElementById(side+'-files');
  root.replaceChildren(el('p','muted','Creazione in corso...'));
  
  try {
    const job = await post('/jobs', { operation: 'mkdir', source_id: id, source_path: newPath });
    let data;
    for(let i=0; i<30; i++) {
      await new Promise(r => setTimeout(r, 1000));
      data = await api('/jobs/' + job.id);
      if (['completed', 'failed', 'cancelled'].includes(data.status)) break;
    }
    if (data.status === 'completed') {
      document.getElementById(side+'-path').value = newPath;
      toast('Cartella creata con successo.');
      await browse(side);
    } else {
      toast(['queued','running'].includes(data.status)?'Creazione ancora in corso: segui il lavoro in Panoramica.':'Errore: '+(data.error||'Creazione annullata.'));
      await browse(side);
    }
  } catch (err) {
    toast(err.message);
  }
}

document.getElementById('mkdir-source').onclick = () => guard(() => handleMkdir('source'));
document.getElementById('mkdir-destination').onclick = () => guard(() => handleMkdir('destination'));


async function handleDelete(side, targetPath, isDir) {
  if (!confirm('Vuoi davvero eliminare ' + (isDir ? 'la cartella' : 'il file') + ' ' + targetPath + '?\nQuesta operazione è irreversibile.')) return;
  const id = document.getElementById(side+'-remote').value;
  toast('Eliminazione in corso...');
  try {
    const job = await post('/jobs', { operation: 'delete', source_id: id, source_path: targetPath, is_file: !isDir, confirm_delete:true });
    let data;
    for(let i=0; i<30; i++) {
      await new Promise(r => setTimeout(r, 1000));
      data = await api('/jobs/' + job.id);
      if (['completed', 'failed', 'cancelled'].includes(data.status)) break;
    }
    if (data.status === 'completed') {
      toast('Eliminato con successo.');
      await browse(side);
    } else {
      toast(['queued','running'].includes(data.status)?'Eliminazione ancora in corso: segui il lavoro in Panoramica.':'Errore: '+(data.error||'Eliminazione annullata.'));
    }
  } catch (err) {
    toast(err.message);
  }
}

const googleResult=new URLSearchParams(location.search).get('google');if(googleResult){history.replaceState({},'',location.pathname);toast({connected:'Google Drive collegato.',cancelled:'Autorizzazione Google annullata.',expired:'Richiesta Google scaduta. Riprova da Collegamenti.',failed:'Google non ha completato l’accesso. Riprova e concedi l’accesso a Drive.'}[googleResult]||'Accesso Google terminato.'); }
try{const options=await api('/auth/options');$('register').hidden=!options.registration;await enter();if(googleResult==='connected')await page('remotes');}catch{ /* Login is the initial screen when no session exists. */ }

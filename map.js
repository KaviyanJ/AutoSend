var map, cityLat=43.4723, cityLon=-80.5449;
var results=[], markers=[], draftQueue=[], idCounter=0;
var companyData = {};

function escHtml(s){
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

window.addEventListener('DOMContentLoaded', function(){
    map = L.map('leaflet-map').setView([cityLat, cityLon], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19
    }).addTo(map);
    map.on('click', function(e){ showManualPopup(e.latlng); });
});

async function geocodeCity(){
    var city = document.getElementById('city-input').value.trim();
    if(!city) return;
    setBtnLoading('geo-btn', true);
    setMsg('Locating ' + city + '...', 'info');
    try {
        var r = await fetch('/api/geocode', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({city:city})});
        var d = await r.json();
        if(d.error){ setMsg(d.error, 'error'); return; }
        cityLat = d.lat; cityLon = d.lon;
        map.setView([d.lat, d.lon], 12);
        setMsg('Showing ' + d.display_name, 'ok');
    } catch(e){ setMsg('Geocoding failed: ' + e, 'error'); }
    finally { setBtnLoading('geo-btn', false); }
}

async function searchCompanies(){
    var query = document.getElementById('query-input').value.trim();
    setBtnLoading('find-btn', true);
    setMsg('Scanning for companies...', 'info');
    clearMarkers();
    document.getElementById('results-list').innerHTML = '<div class="panel-empty"><span class="spinner"></span> Searching...</div>';
    try {
        var r = await fetch('/api/company_search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:query, lat:cityLat, lon:cityLon})});
        var d = await r.json();
        if(d.error){ setMsg(d.error, 'error'); renderResultsList([]); return; }
        results = (d.companies || []).map(function(c){ return Object.assign({}, c, {id:'co'+(++idCounter), status:'unknown', draft:null}); });
        results.forEach(function(co){ companyData[co.id] = co; });
        renderResultsList(results);
        results.forEach(addMarker);
        if(results.length === 0){
            setMsg('No companies found in OSM data here. Try clicking the map to add manually.', 'error');
        } else {
            setMsg('Found ' + results.length + ' companies. Click a pin or list item.', 'ok');
        }
    } catch(e){ setMsg('Search error: ' + e, 'error'); renderResultsList([]); }
    finally { setBtnLoading('find-btn', false); }
}

function makePopupHtml(co){
    return (
        '<div class="map-popup">'
        + '<strong>' + escHtml(co.name) + '</strong>'
        + (co.address ? '<div class="popup-addr">' + escHtml(co.address) + '</div>' : '')
        + '<input class="popup-input" type="text" id="pu-' + co.id + '" value="' + escHtml(co.website||'') + '" placeholder="Company website URL">'
        + '<div class="popup-actions">'
        + '<button class="popup-btn popup-btn-primary" data-action="find" data-coid="' + co.id + '">Find email</button>'
        + '<button class="popup-btn popup-btn-secondary" data-action="addlist" data-coid="' + co.id + '">Add to list</button>'
        + '</div>'
        + '<div class="popup-status" id="ps-' + co.id + '"></div>'
        + '</div>'
    );
}

function addMarker(co){
    var icon = L.divIcon({className:'', html:'<div style="font-size:22px;line-height:1;filter:drop-shadow(0 1px 2px rgba(0,0,0,.4))">&#128205;</div>', iconSize:[24,30], iconAnchor:[12,30], popupAnchor:[0,-28]});
    var m = L.marker([co.lat, co.lon], {icon:icon}).addTo(map);
    m.bindPopup(makePopupHtml(co), {minWidth:230});
    m.coId = co.id;
    markers.push(m);
    m.on('popupopen', function(){
        var popup = m.getPopup().getElement();
        if(!popup) return;
        popup.querySelectorAll('[data-action]').forEach(function(btn){
            btn.addEventListener('click', function(){
                var coid = btn.getAttribute('data-coid');
                var action = btn.getAttribute('data-action');
                if(action === 'find') findEmailPopup(coid);
                else if(action === 'addlist') addToListFromMap(coid);
            });
        });
    });
}

function clearMarkers(){ markers.forEach(function(m){ map.removeLayer(m); }); markers = []; }

function showManualPopup(latlng){
    var id = 'man' + (++idCounter);
    var co = {id:id, name:'', lat:latlng.lat, lon:latlng.lng, website:'', address:'', status:'unknown', draft:null};
    results.push(co); companyData[id] = co;
    var popup = L.popup({minWidth:240}).setLatLng(latlng);
    popup.setContent(
        '<div class="map-popup"><strong>Add company</strong>'
        + '<input class="popup-input" type="text" id="mn-name-' + id + '" placeholder="Company name" style="margin-top:8px">'
        + '<input class="popup-input" type="text" id="pu-' + id + '" placeholder="https://company.com" style="margin-top:4px">'
        + '<div class="popup-actions" style="margin-top:8px">'
        + '<button class="popup-btn popup-btn-primary" data-action="findmanual" data-coid="' + id + '">Find email</button>'
        + '</div><div class="popup-status" id="ps-' + id + '"></div></div>'
    );
    popup.openOn(map);
    map.once('popupopen', function(){
        var el = document.querySelector('.leaflet-popup-content [data-action="findmanual"][data-coid="' + id + '"]');
        if(el) el.addEventListener('click', function(){ findEmailManual(id); });
    });
}

async function findEmailManual(id){
    var name = document.getElementById('mn-name-' + id).value.trim();
    var url  = document.getElementById('pu-' + id).value.trim();
    if(!name){ setPopupStatus(id,'Enter a company name.','error'); return; }
    if(!url){  setPopupStatus(id,'Enter a website URL.','error');  return; }
    var co = companyData[id]; if(co){ co.name=name; co.website=url; }
    await findEmailForCo(id, name, url);
}

async function findEmailPopup(id){
    var urlEl = document.getElementById('pu-' + id);
    var url   = urlEl ? urlEl.value.trim() : '';
    var co    = companyData[id];
    var name  = co ? co.name : '';
    if(!url){ setPopupStatus(id,'Enter the company website URL first.','error'); return; }
    if(co) co.website = url;
    await findEmailForCo(id, name, url);
}

async function findEmailForCo(id, name, url){
    setPopupStatus(id, '<span class="spinner"></span> Scraping website...', 'info');
    updateResultStatus(id, 'searching');
    try {
        var r = await fetch('/api/quick_draft', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name, url:url})});
        var d = await r.json();
        if(d.error){ setPopupStatus(id,'&#10060; '+d.error,'error'); updateResultStatus(id,'no-email'); return; }
        var co = companyData[id];
        if(co){ co.status='ready'; co.draft=d.draft; }
        setPopupStatus(id,
            '&#9989; Found: <strong>' + escHtml(d.draft.email) + '</strong>'
            + '&nbsp;<button class="popup-btn popup-btn-primary" data-action="queue" data-coid="' + id + '">Queue &amp; send</button>',
            'ok');
        var psEl = document.getElementById('ps-' + id);
        if(psEl){ psEl.querySelectorAll('[data-action="queue"]').forEach(function(btn){ btn.addEventListener('click', function(){ queueDraft(btn.getAttribute('data-coid')); }); }); }
        updateResultStatus(id,'ready'); renderResultsList(results);
    } catch(e){ setPopupStatus(id,'&#10060; Error: '+e,'error'); updateResultStatus(id,'error'); }
}

function setPopupStatus(id,html,type){
    var el=document.getElementById('ps-'+id); if(!el)return;
    var cls=type==='error'?'map-msg-error':type==='ok'?'map-msg-ok':'map-msg-info';
    el.innerHTML='<div class="map-msg '+cls+'" style="margin-top:8px">'+html+'</div>';
}
function updateResultStatus(id,status){ var co=companyData[id]; if(co)co.status=status; }

function queueDraft(id){
    var co=companyData[id]; if(!co||!co.draft)return;
    if(draftQueue.find(function(d){return d.coId===id;}))return;
    draftQueue.push({coId:id,name:co.name,draft:co.draft});
    renderQueue(); setPopupStatus(id,'&#128203; Added to send queue!','ok');
}
function removeFromQueue(coId){ draftQueue=draftQueue.filter(function(d){return d.coId!==coId;}); renderQueue(); }

function renderQueue(){
    var el=document.getElementById('draft-queue');
    var cnt=document.getElementById('queue-count');
    var act=document.getElementById('queue-actions');
    cnt.textContent=draftQueue.length;
    if(!draftQueue.length){ act.style.display='none'; el.innerHTML='<div class="panel-empty">No emails queued yet.</div>'; return; }
    act.style.display='';
    el.innerHTML=draftQueue.map(function(item){
        return('<div class="queue-item"><div><div class="qi-name">'+escHtml(item.name)+'</div><div class="qi-email">'+escHtml(item.draft.email)+'</div></div>'
            +'<button class="queue-remove" data-action="removequeue" data-coid="'+item.coId+'" title="Remove">&#215;</button></div>');
    }).join('');
    el.querySelectorAll('[data-action="removequeue"]').forEach(function(btn){ btn.addEventListener('click',function(){ removeFromQueue(btn.getAttribute('data-coid')); }); });
}

async function sendAllQueued(){
    if(!draftQueue.length)return;
    var btn=document.querySelector('#queue-actions .btn-success');
    btn.disabled=true; btn.innerHTML='<span class="spinner"></span> Sending...';
    var ok=0,fail=0; var items=[...draftQueue];
    for(var i=0;i<items.length;i++){
        var item=items[i];
        try {
            var r=await fetch('/api/send_single',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:item.name,email:item.draft.email,subject:item.draft.subject,body:item.draft.body,url:item.draft.url})});
            var d=await r.json(); if(d.ok)ok++;else fail++;
        } catch(e){fail++;}
    }
    draftQueue=[]; renderQueue();
    setMsg('Sent '+ok+' email(s).'+(fail?' '+fail+' failed.':''),'ok');
    btn.disabled=false; btn.innerHTML='&#9993; Send all queued';
}

async function addAllToBulkDrafts(){
    if(!draftQueue.length)return;
    var r=await fetch('/api/add_to_session_drafts',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({drafts:draftQueue.map(function(item){return item.draft;})})});
    var d=await r.json();
    if(d.ok){setMsg('Moved '+draftQueue.length+' draft(s) to Preview. Redirecting...','ok');draftQueue=[];renderQueue();setTimeout(function(){window.location.href='/preview';},1400);}
}

function addToListFromMap(id){
    var co=companyData[id]; if(!co)return;
    var urlEl=document.getElementById('pu-'+id);
    var url=urlEl?urlEl.value.trim():(co.website||'');
    if(!url){setPopupStatus(id,'Enter a website URL first.','error');return;}
    fetch('/api/add_to_paste_list',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({line:co.name+' | '+url})})
    .then(function(){setPopupStatus(id,'&#128203; Added to paste list.','ok');});
}

function renderResultsList(list){
    var el=document.getElementById('results-list');
    if(!list||!list.length){el.innerHTML='<div class="panel-empty">No companies found. Try a different city/query, or click the map to add manually.</div>';return;}
    el.innerHTML=list.map(function(co){
        var dot=co.status==='ready'?'<span style="color:#059669">&#9679;</span> ':co.status==='searching'?'<span class="spinner"></span> ':co.status==='no-email'?'<span style="color:#9ca3af">&#9679;</span> ':'';
        return('<div class="result-item" data-flyto="'+co.id+'">'
            +'<div class="ri-name">'+dot+escHtml(co.name)+'</div>'
            +(co.address?'<div class="ri-addr">'+escHtml(co.address)+'</div>':'')
            +'<div class="ri-actions">'
            +(co.status==='ready'&&co.draft
                ?'<button class="btn btn-success btn-sm" data-action="queuedraft" data-coid="'+co.id+'">Queue</button>'
                :'<button class="btn btn-secondary btn-sm" data-action="flytofindemail" data-coid="'+co.id+'">Find email</button>')
            +'</div></div>');
    }).join('');
    el.querySelectorAll('[data-flyto]').forEach(function(item){
        item.addEventListener('click',function(e){ if(e.target.closest('[data-action]'))return; flyTo(item.getAttribute('data-flyto')); });
    });
    el.querySelectorAll('[data-action="queuedraft"]').forEach(function(btn){ btn.addEventListener('click',function(e){e.stopPropagation();queueDraft(btn.getAttribute('data-coid'));}); });
    el.querySelectorAll('[data-action="flytofindemail"]').forEach(function(btn){ btn.addEventListener('click',function(e){e.stopPropagation();flyToAndFind(btn.getAttribute('data-coid'));}); });
}

function flyTo(id){ var co=companyData[id]; if(!co)return; map.flyTo([co.lat,co.lon],15,{duration:0.8}); var m=markers.find(function(mk){return mk.coId===id;}); if(m)m.openPopup(); }
function flyToAndFind(id){ var co=companyData[id]; if(!co)return; map.flyTo([co.lat,co.lon],15,{duration:0.6}); var m=markers.find(function(mk){return mk.coId===id;}); if(m){m.openPopup();setTimeout(function(){findEmailPopup(id);},700);} }
function setMsg(text,type){ var cls=type==='error'?'map-msg-error':type==='ok'?'map-msg-ok':'map-msg-info'; document.getElementById('map-msg').innerHTML='<div class="map-msg '+cls+'">'+text+'</div>'; }
function setBtnLoading(id,on){ var btn=document.getElementById(id); if(!btn)return; btn.disabled=on; if(on){btn._orig=btn.innerHTML;btn.innerHTML='<span class="spinner"></span>';}else{btn.innerHTML=btn._orig||btn.innerHTML;} }

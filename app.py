"""AutoSend — EE co-op outreach. Flask routing and UI only; logic lives in core.py."""

import os
import io
import csv
import re
import secrets
import datetime
from html import escape
from typing import List, Dict

from flask import Flask, request, redirect, url_for, session, jsonify, send_file, g
import requests

import core
from core import (
    load_cfg, save_cfg, daily_limit,
    load_saved_lists, save_list, delete_saved_list,
    load_blocklist, save_blocklist, add_to_blocklist, blocked_reason, domain_of,
    read_log, contacted_keys, contacted_domains, count_sent_today,
    append_log, make_log_row, migrate_log,
    find_emails, find_emails_bulk, best_email,
    GmailBatch, send_one,
    focus_hook, make_body, make_subject, parse_company_lines,
    load_drafts, save_drafts, purge_old_drafts, clear_cache,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

# ── Inlined assets ─────────────────────────────────────────────────────────────
# Kept inside app.py deliberately: a single file has no static/ folder to lose
# and nothing extra to remember when deploying.
APP_CSS = """*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;color:#1e293b;display:flex;min-height:100vh}
.sidebar{width:220px;min-height:100vh;background:#0f172a;display:flex;flex-direction:column;flex-shrink:0}
.sidebar-brand{padding:20px 18px 16px;border-bottom:1px solid rgba(255,255,255,.08)}
.sidebar-brand h1{color:#f8fafc;font-size:17px;font-weight:700;letter-spacing:-.3px}
.sidebar-brand p{color:#64748b;font-size:11px;margin-top:3px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 18px;color:#94a3b8;text-decoration:none;font-size:13.5px;transition:all .15s}
.nav-item:hover{background:rgba(255,255,255,.06);color:#f1f5f9}
.nav-item.active{background:rgba(59,130,246,.18);color:#60a5fa;border-right:3px solid #3b82f6}
.nav-item span{font-size:13px}
.sidebar-footer{margin-top:auto;padding:14px 18px;border-top:1px solid rgba(255,255,255,.06)}
.sidebar-footer .term-badge{background:rgba(59,130,246,.15);color:#93c5fd;padding:5px 10px;border-radius:6px;font-size:11px;font-weight:600;display:block;text-align:center}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.topbar{background:#fff;border-bottom:1px solid #e2e8f0;padding:14px 28px;display:flex;align-items:center;justify-content:space-between}
.topbar h2{font-size:18px;font-weight:700;color:#0f172a}
.quota-pill{display:flex;gap:16px}
.quota-stat{text-align:center}
.quota-stat .val{font-size:18px;font-weight:700;color:#1e293b}
.quota-stat .lbl{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
.page{padding:28px;flex:1}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:22px;margin-bottom:18px}
.card h3{font-size:14px;font-weight:700;color:#374151;margin-bottom:14px}
.form-group{margin-bottom:16px}
.form-label{display:block;font-size:12.5px;font-weight:600;color:#374151;margin-bottom:5px}
input[type=text],input[type=number],input[type=email],textarea,select{width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:13.5px;color:#1e293b;background:#fff;outline:none;transition:border .15s}
input:focus,textarea:focus,select:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1)}
textarea{resize:vertical;min-height:100px}
code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border:none;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;transition:all .15s}
.btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}
.btn-success{background:#059669;color:#fff}.btn-success:hover{background:#047857}
.btn-danger{background:#dc2626;color:#fff}.btn-danger:hover{background:#b91c1c}
.btn-warn{background:#b45309;color:#fff}.btn-warn:hover{background:#92400e}
.btn-secondary{background:#f1f5f9;color:#374151;border:1px solid #d1d5db}.btn-secondary:hover{background:#e2e8f0}
.btn-sm{padding:5px 11px;font-size:12px}
.btn:disabled{opacity:.55;cursor:not-allowed}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f8fafc;padding:9px 12px;text-align:left;font-weight:600;color:#374151;border-bottom:2px solid #e2e8f0;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid #f1f5f9;vertical-align:top}
tr:hover td{background:#fafafa}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}
.badge-green{background:#dcfce7;color:#166534}
.badge-red{background:#fee2e2;color:#991b1b}
.badge-yellow{background:#fef3c7;color:#92400e}
.badge-blue{background:#dbeafe;color:#1e40af}
.badge-gray{background:#f1f5f9;color:#475569}
.text-muted{color:#64748b}
.empty-state{text-align:center;padding:48px 20px;color:#64748b}
.empty-state .icon{font-size:40px;margin-bottom:12px}
.page-actions{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px}
.form-hint{font-size:11.5px;color:#64748b;margin-top:4px}
.alert{padding:10px 14px;border-radius:7px;font-size:13px;margin-bottom:14px}
.alert-success{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0}
.alert-error{background:#fef2f2;color:#991b1b;border:1px solid #fecaca}
.alert-info{background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe}
.alert-warn{background:#fffbeb;color:#92400e;border:1px solid #fde68a}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(0,0,0,.1);border-top-color:#3b82f6;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.tabs{display:flex;gap:2px;border-bottom:2px solid #e2e8f0;margin-bottom:18px}
.tab{padding:8px 16px;font-size:13px;font-weight:600;color:#64748b;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;text-decoration:none}
.tab.active{color:#3b82f6;border-bottom-color:#3b82f6}
.tab:hover{color:#1e293b}
.map-wrap{display:flex;height:calc(100vh - 70px);overflow:hidden}
.map-panel{width:320px;flex-shrink:0;background:#fff;border-right:1px solid #e2e8f0;display:flex;flex-direction:column;overflow:hidden}
.map-panel-top{padding:16px;border-bottom:1px solid #e2e8f0}
.map-panel-scroll{flex:1;overflow-y:auto;padding:12px 16px}
#leaflet-map{flex:1}
.panel-section-title{font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px}
.panel-empty{color:#94a3b8;font-size:12.5px;text-align:center;padding:16px 0}
.result-item{padding:10px;border:1px solid #e2e8f0;border-radius:7px;margin-bottom:6px;cursor:pointer;transition:all .15s}
.result-item:hover{border-color:#93c5fd;background:#eff6ff}
.ri-name{font-size:13px;font-weight:600;color:#1e293b}
.ri-addr{font-size:11.5px;color:#64748b;margin-top:2px}
.ri-actions{margin-top:7px}
.queue-item{display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:#f8fafc;border-radius:6px;margin-bottom:5px}
.qi-name{font-size:13px;font-weight:600}
.qi-email{font-size:11px;color:#64748b}
.queue-remove{background:none;border:none;cursor:pointer;font-size:16px;color:#94a3b8;padding:0 4px}
.queue-remove:hover{color:#dc2626}
.map-popup{min-width:210px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.popup-addr{font-size:11.5px;color:#64748b;margin:4px 0 8px}
.popup-input{width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:5px;font-size:12.5px;margin-bottom:8px;box-sizing:border-box}
.popup-actions{display:flex;gap:6px;margin-bottom:4px}
.popup-btn{padding:5px 10px;border:none;border-radius:5px;font-size:12px;font-weight:600;cursor:pointer}
.popup-btn-primary{background:#3b82f6;color:#fff}
.popup-btn-secondary{background:#f1f5f9;color:#374151;border:1px solid #d1d5db}
.map-msg{font-size:12px;padding:6px 10px;border-radius:5px}
.map-msg-info{background:#eff6ff;color:#1e40af}
.map-msg-ok{background:#f0fdf4;color:#166534}
.map-msg-error{background:#fef2f2;color:#991b1b}
#busy-overlay{position:fixed;inset:0;background:rgba(15,23,42,.55);display:none;align-items:center;justify-content:center;z-index:9999}
#busy-overlay .box{background:#fff;border-radius:12px;padding:26px 34px;text-align:center;max-width:340px}
#busy-overlay .box p{font-size:13px;color:#475569;margin-top:10px;line-height:1.5}"""

MAP_JS = """var map, cityLat=43.4723, cityLon=-80.5449;
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
function setBtnLoading(id,on){ var btn=document.getElementById(id); if(!btn)return; btn.disabled=on; if(on){btn._orig=btn.innerHTML;btn.innerHTML='<span class="spinner"></span>';}else{btn.innerHTML=btn._orig||btn.innerHTML;} }"""


_MIGRATION_NOTE = migrate_log()
purge_old_drafts()


# ══════════════════════════════════════════════════════════════════════════════
# Request helpers
# ══════════════════════════════════════════════════════════════════════════════
def log_rows() -> List[Dict[str, str]]:
    """Read the CSV once per request instead of once per caller."""
    if not hasattr(g, "_log_rows"):
        g._log_rows = read_log()
    return g._log_rows


def invalidate_log():
    if hasattr(g, "_log_rows"):
        del g._log_rows


def sid() -> str:
    s = session.get("sid")
    if not s:
        s = secrets.token_urlsafe(16)
        session["sid"] = s
    return s


def drafts() -> List[dict]:
    return load_drafts(sid())


def set_drafts(d: List[dict]) -> None:
    save_drafts(sid(), d)


def e(v) -> str:
    return escape(str(v or ""), quote=True)


# ══════════════════════════════════════════════════════════════════════════════
# Layout
# ══════════════════════════════════════════════════════════════════════════════
NAV = [("dashboard", "&#127968;", "Dashboard", "/"),
       ("campaign",  "&#9993;",   "Campaign",  "/campaign"),
       ("map",       "&#128506;", "Map Search", "/map"),
       ("preview",   "&#128203;", "Preview",   "/preview"),
       ("history",   "&#128202;", "History",   "/history"),
       ("blocklist", "&#128683;", "Blocklist", "/blocklist"),
       ("settings",  "&#9881;",   "Settings",  "/settings")]


def layout(title: str, active: str, body: str, cfg: dict, head: str = "") -> str:
    rows = log_rows()
    limit = daily_limit()
    sent_today = count_sent_today(rows)
    remaining = max(0, limit - sent_today)
    nav = "".join(
        f'<a href="{href}" class="nav-item{" active" if active == key else ""}">{icon}<span>{label}</span></a>'
        for key, icon, label, href in NAV
    )
    n_blocked = len(load_blocklist().get("domains", []))
    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)} – AutoSend</title>"
        "<style>" + APP_CSS + "</style>"
        f"{head}</head><body>"
        "<nav class='sidebar'>"
        "<div class='sidebar-brand'><h1>&#9889; AutoSend</h1><p>EE Co-op Outreach</p></div>"
        + nav +
        f"<div class='sidebar-footer'><span class='term-badge'>{e(cfg.get('internship_term',''))}</span></div>"
        "</nav><div class='main'><div class='topbar'>"
        f"<h2>{e(title)}</h2><div class='quota-pill'>"
        f"<div class='quota-stat'><div class='val'>{sent_today}</div><div class='lbl'>Sent today</div></div>"
        f"<div class='quota-stat'><div class='val'>{remaining}</div><div class='lbl'>Remaining</div></div>"
        f"<div class='quota-stat'><div class='val'>{limit}</div><div class='lbl'>Daily limit</div></div>"
        f"<div class='quota-stat'><div class='val'>{n_blocked}</div><div class='lbl'>Blocked</div></div>"
        "</div></div>"
        f"<div class='page'>{body}</div></div>"
        "<div id='busy-overlay'><div class='box'><span class='spinner' style='width:26px;height:26px;border-width:3px'></span>"
        "<p id='busy-text'>Working…</p></div></div>"
        "<script>"
        "function showBusy(t){var o=document.getElementById('busy-overlay');"
        "document.getElementById('busy-text').textContent=t||'Working…';o.style.display='flex';}"
        "</script>"
        "</body></html>"
    )


def alert(msg: str, kind: str = "info") -> str:
    return f'<div class="alert alert-{kind}">{e(msg)}</div>' if msg else ""


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    cfg = load_cfg()
    rows = log_rows()
    limit = daily_limit()
    sent_today = count_sent_today(rows)
    d = drafts()
    term = cfg["internship_term"]
    this_term = len(contacted_keys(rows, term))

    stat = lambda val, lbl, colour: (
        f'<div class="card" style="margin:0;text-align:center">'
        f'<div style="font-size:28px;font-weight:800;color:{colour}">{val}</div>'
        f'<div style="font-size:12px;color:#64748b;margin-top:4px">{lbl}</div></div>'
    )

    body = (
        alert(_MIGRATION_NOTE or "", "warn") +
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px">'
        + stat(sent_today, "Sent today", "#3b82f6")
        + stat(max(0, limit - sent_today), "Remaining", "#059669")
        + stat(this_term, "Contacted this term", "#7c3aed")
        + stat(len(rows), "All-time emails", "#0f172a")
        + '</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">'
        '<div class="card"><h3>&#9889; Quick actions</h3>'
        '<div style="display:flex;flex-direction:column;gap:10px">'
        '<a href="/campaign" class="btn btn-primary">&#9993; New campaign</a>'
        '<a href="/map" class="btn btn-secondary">&#128506; Map search</a>'
        + (f'<a href="/preview" class="btn btn-success">&#128203; Review {len(d)} draft(s)</a>' if d else '')
        + '</div></div>'
        '<div class="card"><h3>&#127775; Current term</h3>'
        f'<div style="font-size:22px;font-weight:700;color:#3b82f6;margin-bottom:8px">{e(term)}</div>'
        '<p style="font-size:13px;color:#64748b;margin-bottom:12px">'
        'Companies contacted in earlier terms are eligible again. Anything on the '
        'blocklist is skipped in every term.</p>'
        '<a href="/settings" class="btn btn-secondary btn-sm">Change term</a>'
        '</div></div>'
    )
    return layout("Dashboard", "dashboard", body, cfg)


# ══════════════════════════════════════════════════════════════════════════════
# Campaign
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/campaign", methods=["GET", "POST"])
def campaign():
    cfg = load_cfg()
    msg, kind = "", ""
    saved = load_saved_lists()
    pending = session.get("pending_companies", "")

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "upload_csv":
            f = request.files.get("csv_file")
            if f and f.filename:
                reader = csv.reader(io.StringIO(f.read().decode("utf-8", errors="replace")))
                lines = []
                for i, row in enumerate(reader):
                    if not row:
                        continue
                    if i == 0 and row[0].strip().lower() in ("company", "name", "company name"):
                        continue
                    if len(row) < 2 or not row[0].strip() or not row[1].strip():
                        continue
                    url = row[1].strip()
                    if not url.startswith("http"):
                        url = "https://" + url
                    parts = [row[0].strip(), url]
                    if len(row) > 2 and row[2].strip():
                        parts.append(row[2].strip())
                    if len(row) > 3 and row[3].strip():
                        parts.append(row[3].strip())
                    lines.append(" | ".join(parts))
                if lines:
                    session["pending_companies"] = pending = "\n".join(lines)
                    msg, kind = f"Imported {len(lines)} companies.", "success"
                else:
                    msg, kind = "No valid rows found in that CSV.", "error"

        elif action == "save_list":
            name = request.form.get("list_name", "").strip()
            content = request.form.get("company_lines", "").strip()
            if name and content:
                save_list(name, content)
                session["pending_companies"] = pending = content
                saved = load_saved_lists()
                msg, kind = f'Saved list "{name}".', "success"
            else:
                msg, kind = "Enter a name and at least one company.", "error"

        elif action == "load_list":
            name = request.form.get("load_name", "")
            for l in saved:
                if l["name"] == name:
                    session["pending_companies"] = pending = l["content"]
                    msg, kind = f'Loaded "{name}".', "success"
                    break

        elif action == "delete_list":
            delete_saved_list(request.form.get("delete_name", ""))
            saved = load_saved_lists()
            msg, kind = "List deleted.", "success"

        elif action == "build":
            raw = request.form.get("company_lines", "").strip()
            session["pending_companies"] = pending = raw
            try:
                max_drafts = min(max(int(request.form.get("max_drafts", "20") or 20), 1), 200)
            except ValueError:
                max_drafts = 20
            use_cache = request.form.get("use_cache") == "on"
            built, note = build_drafts(raw, cfg, max_drafts, use_cache)
            if built:
                return redirect(url_for("preview"))
            msg, kind = note, "error"

    # ── render ────────────────────────────────────────────────────────────────
    saves_html = ""
    if saved:
        items = "".join(
            '<div style="display:flex;align-items:center;justify-content:space-between;'
            'padding:8px 12px;background:#f8fafc;border-radius:6px;font-size:13px">'
            f'<span style="font-weight:600">{e(l["name"])}</span><div style="display:flex;gap:6px">'
            f'<form method="post" style="display:inline"><input type="hidden" name="load_name" value="{e(l["name"])}">'
            '<button class="btn btn-secondary btn-sm" name="action" value="load_list">Load</button></form>'
            f'<form method="post" style="display:inline"><input type="hidden" name="delete_name" value="{e(l["name"])}">'
            '<button class="btn btn-danger btn-sm" name="action" value="delete_list">Delete</button></form>'
            '</div></div>'
            for l in saved
        )
        saves_html = ('<div class="card"><h3>&#128190; Saved lists</h3>'
                      f'<div style="display:flex;flex-direction:column;gap:6px">{items}</div></div>')

    body = (
        alert(msg, kind) +
        '<div class="tabs">'
        '<a class="tab active" href="/campaign">&#128196; Paste / Upload</a>'
        '<a class="tab" href="/map">&#128506; Map search</a>'
        '</div>'
        '<div class="card"><h3>&#128196; Add companies</h3>'
        '<form method="post" enctype="multipart/form-data">'
        '<div class="form-group"><label class="form-label">Upload CSV</label>'
        '<input type="file" name="csv_file" accept=".csv" style="font-size:13px">'
        '<div class="form-hint">Columns: Company, URL, Location, Focus (last two optional). '
        '<a href="/csv_template" style="color:#3b82f6">Download template</a></div></div>'
        '<button class="btn btn-secondary btn-sm" name="action" value="upload_csv">&#128196; Import CSV</button>'
        '</form><hr style="margin:18px 0;border:none;border-top:1px solid #e2e8f0">'
        '<form method="post" onsubmit="if(event.submitter&&event.submitter.value===\'build\')'
        'showBusy(\'Scraping company sites in parallel — this takes a few seconds per batch.\')">'
        '<div class="form-group"><label class="form-label">Company list</label>'
        f'<textarea name="company_lines" rows="9" placeholder="Acme Power | https://acme.com | Waterloo, ON | power">{e(pending)}</textarea>'
        '<div class="form-hint">Format: <code>Company | https://url.com | City, Region | focus</code> — one per line.<br>'
        '<strong>Focus</strong> is optional and sets the opening line: '
        '<code>power</code>, <code>pcb</code>, <code>robotics</code>, <code>semiconductor</code>, '
        '<code>embedded</code>, <code>test</code>.</div></div>'
        '<div style="display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;margin-bottom:14px">'
        '<div class="form-group" style="margin:0"><label class="form-label">Max drafts</label>'
        '<input type="number" name="max_drafts" min="1" max="200" value="25" style="width:90px"></div>'
        '<label style="font-size:12.5px;color:#374151;display:flex;align-items:center;gap:6px;padding-bottom:9px">'
        '<input type="checkbox" name="use_cache" checked> Use cached scrape results</label>'
        '<button class="btn btn-primary" name="action" value="build">&#128269; Build drafts</button>'
        '</div><hr style="margin:14px 0;border:none;border-top:1px solid #e2e8f0">'
        '<div style="display:flex;gap:8px;align-items:flex-end">'
        '<div class="form-group" style="flex:1;margin:0"><label class="form-label">Save list as</label>'
        '<input type="text" name="list_name" placeholder="e.g. Waterloo Hardware"></div>'
        '<button class="btn btn-secondary" name="action" value="save_list">&#128190; Save</button>'
        '</div></form></div>' + saves_html
    )
    return layout("Campaign", "campaign", body, cfg)


def build_drafts(raw: str, cfg: dict, max_drafts: int, use_cache: bool):
    """Returns (built_any, message)."""
    companies = parse_company_lines(raw)
    if not companies:
        return False, "No valid company lines. Use: Company | https://url.com"

    rows = log_rows()
    term = cfg["internship_term"]
    seen_pairs = contacted_keys(rows, term)
    seen_domains = contacted_domains(rows, term)

    # Drop blocked companies before doing any network work.
    kept, n_blocked = [], 0
    for co in companies:
        if blocked_reason(co["url"], co["name"]):
            n_blocked += 1
        else:
            kept.append(co)

    email_map = find_emails_bulk(kept, use_cache=use_cache)

    out: List[dict] = []
    n_no_email, n_dupe = 0, 0
    used_domains = set()
    for co in kept:
        if len(out) >= max_drafts:
            break
        dom = domain_of(co["url"])
        pick = best_email(email_map.get(co["url"], []), dom)
        if not pick:
            n_no_email += 1
            continue
        if blocked_reason(pick):
            n_blocked += 1
            continue
        edom = domain_of(pick)
        if edom in used_domains or edom in seen_domains:
            n_dupe += 1
            continue
        if (co["name"].strip().lower(), pick.lower()) in seen_pairs:
            n_dupe += 1
            continue
        used_domains.add(edom)
        out.append({
            "company": co["name"],
            "email": pick,
            "subject": make_subject(co["name"], cfg),
            "body": make_body(co["name"], focus_hook(co.get("focus", ""), cfg), cfg),
            "url": co["url"],
        })

    set_drafts(out)
    parts = [f"{len(out)} draft(s) built"]
    if n_blocked:
        parts.append(f"{n_blocked} blocked")
    if n_dupe:
        parts.append(f"{n_dupe} already contacted")
    if n_no_email:
        parts.append(f"{n_no_email} with no usable email")
    return bool(out), " · ".join(parts) + "."


@app.route("/csv_template")
def csv_template():
    content = ("Company,URL,Location,Focus\n"
               "Acme Power Systems,https://acmepow.com,Waterloo ON,power\n"
               "Volta Semiconductor,https://volta.example,Toronto ON,semiconductor\n")
    return send_file(io.BytesIO(content.encode()), mimetype="text/csv",
                     as_attachment=True, download_name="autosend_template.csv")


# ══════════════════════════════════════════════════════════════════════════════
# Preview
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/preview", methods=["GET", "POST"])
def preview():
    cfg = load_cfg()
    d = drafts()
    msg, kind = "", ""

    if request.method == "POST":
        action = request.form.get("action", "")
        for i, draft in enumerate(d):
            draft["subject"] = request.form.get(f"subject_{i}", draft.get("subject", "")).strip()
            draft["body"] = request.form.get(f"body_{i}", draft.get("body", ""))
        sel = sorted({int(x) for x in request.form.getlist("selected") if x.isdigit()})
        sel = [i for i in sel if i < len(d)]

        if not sel:
            msg, kind = "Nothing selected.", "error"

        elif action in ("reject", "reject_block"):
            entries = [make_log_row(cfg, d[i], "REJECTED") for i in sel]
            append_log(entries)
            invalidate_log()
            blocked = []
            if action == "reject_block":
                for i in sel:
                    dom = add_to_blocklist(d[i]["email"], f'Blocked from preview on {datetime.date.today()}')
                    if dom:
                        blocked.append(dom)
            set_drafts([x for j, x in enumerate(d) if j not in set(sel)])
            msg = f"Rejected {len(sel)} draft(s)."
            if blocked:
                msg += f" Blocked: {', '.join(sorted(set(blocked)))}."
            kind = "success"
            d = drafts()

        elif action == "send":
            limit = daily_limit()
            remaining = max(0, limit - count_sent_today(log_rows()))
            if remaining <= 0:
                msg, kind = f"Daily limit of {limit} already reached.", "error"
            else:
                trimmed = len(sel) - min(len(sel), remaining)
                sel = sel[:remaining]
                entries, ok_n, fail_n, last_err = [], 0, 0, ""
                try:
                    with GmailBatch() as sender:   # one connection, one login
                        for i in sel:
                            ok, err = sender.send(d[i]["email"], d[i]["subject"], d[i]["body"])
                            if ok:
                                ok_n += 1
                            else:
                                fail_n += 1
                                last_err = err
                            entries.append(make_log_row(cfg, d[i], "SENT" if ok else "FAILED"))
                except Exception as ex:
                    msg, kind = f"Could not connect to Gmail: {ex}", "error"
                    entries = []
                if entries:
                    append_log(entries)
                    invalidate_log()
                    set_drafts([x for j, x in enumerate(d) if j not in set(sel)])
                    d = drafts()
                    msg = f"Sent {ok_n}."
                    if fail_n:
                        msg += f" {fail_n} failed ({last_err[:120]})."
                    if trimmed:
                        msg += f" {trimmed} held back by the daily limit."
                    kind = "success" if not fail_n else "warn"

    rows = log_rows()
    remaining = max(0, daily_limit() - count_sent_today(rows))

    if not d:
        body = (alert(msg, kind) +
                '<div class="empty-state"><div class="icon">&#128237;</div>'
                '<p style="margin-bottom:16px">No drafts queued.</p>'
                '<a class="btn btn-primary" href="/campaign">New campaign</a></div>')
        return layout("Preview", "preview", body, cfg)

    trs = ""
    for i, draft in enumerate(d):
        trs += (
            "<tr>"
            f"<td style='width:34px'><input type='checkbox' name='selected' value='{i}'></td>"
            f"<td><strong>{e(draft['company'])}</strong><div class='text-muted' style='font-size:11px'>"
            f"{e(domain_of(draft['email']))}</div></td>"
            f"<td style='white-space:nowrap;font-size:12.5px'>{e(draft['email'])}</td>"
            f"<td><input type='text' name='subject_{i}' value=\"{e(draft['subject'])}\" style='min-width:230px'></td>"
            f"<td><button type='button' class='btn btn-secondary btn-sm' onclick='toggleBody(this,{i})'>Edit body</button>"
            f"<textarea id='body_{i}' name='body_{i}' style='display:none;min-width:360px;margin-top:6px' rows='16'>"
            f"{escape(draft['body'])}</textarea></td>"
            f"<td><a href='{e(draft['url'])}' target='_blank' rel='noopener' style='font-size:12px'>&#8599;</a></td>"
            "</tr>"
        )

    body = (
        alert(msg, kind) +
        '<div class="page-actions"><span class="text-muted" style="font-size:13px">'
        f'{len(d)} draft(s) queued &middot; {remaining} send(s) left today</span></div>'
        '<div class="card"><form method="post" onsubmit="if(event.submitter&&event.submitter.value===\'send\')'
        'showBusy(\'Sending over a single SMTP connection…\')">'
        '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;align-items:center">'
        '<button class="btn btn-success" name="action" value="send">&#9993; Send selected</button>'
        '<button class="btn btn-danger" name="action" value="reject">&#128465; Reject</button>'
        '<button class="btn btn-warn" name="action" value="reject_block" '
        'title="Reject and never contact this domain again">&#128683; Reject &amp; block domain</button>'
        '<a class="btn btn-secondary" href="/campaign">&#8592; Back</a></div>'
        '<div class="tbl-wrap"><table><thead><tr>'
        '<th><input type="checkbox" onclick="toggleAll(this)" title="Select all"></th>'
        '<th>Company</th><th>Email</th><th>Subject</th><th>Body</th><th>Site</th>'
        f'</tr></thead><tbody>{trs}</tbody></table></div></form></div>'
        '<script>'
        'function toggleAll(s){document.querySelectorAll(\'input[name="selected"]\').forEach(c=>c.checked=s.checked)}'
        'function toggleBody(b,i){var t=document.getElementById("body_"+i);'
        'var open=t.style.display==="none";t.style.display=open?"block":"none";'
        'b.textContent=open?"Hide body":"Edit body"}'
        '</script>'
    )
    return layout("Preview", "preview", body, cfg)


# ══════════════════════════════════════════════════════════════════════════════
# History
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/history")
def history():
    cfg = load_cfg()
    rows = list(reversed(log_rows()))
    q = request.args.get("q", "").strip().lower()
    if q:
        rows = [r for r in rows if q in " ".join(r.values()).lower()]

    if not rows:
        body = ('<div class="empty-state"><div class="icon">&#128202;</div>'
                '<p>Nothing logged yet.</p></div>')
        return layout("History", "history", body, cfg)

    trs = ""
    for r in rows[:1000]:
        st = r.get("status", "").upper()
        cls = "green" if st == "SENT" else "red" if st in ("FAILED", "REJECTED") else "yellow"
        term = r.get("term", "")
        term_badge = (f'<span class="badge badge-blue" style="font-size:10px">{e(term)}</span>'
                      if term else '<span class="badge badge-gray" style="font-size:10px">—</span>')
        trs += (
            f"<tr><td style='white-space:nowrap'>{e(r.get('date',''))}</td>"
            f"<td>{term_badge}</td>"
            f"<td><strong>{e(r.get('company',''))}</strong></td>"
            f"<td style='font-size:12.5px'>{e(r.get('email',''))}</td>"
            f"<td><span class='badge badge-{cls}'>{e(st)}</span></td>"
            f"<td style='font-size:12px'><a href='{e(r.get('source_url',''))}' target='_blank' rel='noopener'>&#8599;</a></td></tr>"
        )

    body = (
        '<div class="card"><form method="get" style="display:flex;gap:8px;margin-bottom:14px">'
        f'<input type="text" name="q" value="{e(q)}" placeholder="Filter by company, email, term, status" style="flex:1">'
        '<button class="btn btn-secondary btn-sm">Filter</button>'
        '<a class="btn btn-secondary btn-sm" href="/history">Clear</a>'
        '<a class="btn btn-secondary btn-sm" href="/export">&#11015; CSV</a></form>'
        '<div class="tbl-wrap"><table><thead><tr>'
        '<th>Date</th><th>Term</th><th>Company</th><th>Email</th><th>Status</th><th>Source</th>'
        f'</tr></thead><tbody>{trs}</tbody></table></div></div>'
    )
    return layout("History", "history", body, cfg)


@app.route("/export")
def export():
    if not os.path.exists(core.LOG_PATH):
        return redirect(url_for("history"))
    return send_file(core.LOG_PATH, mimetype="text/csv", as_attachment=True,
                     download_name=f"autosend_log_{datetime.date.today()}.csv")


# ══════════════════════════════════════════════════════════════════════════════
# Blocklist
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/blocklist", methods=["GET", "POST"])
def blocklist_page():
    cfg = load_cfg()
    msg, kind = "", ""
    if request.method == "POST":
        raw = request.form.get("domains", "")
        entries = [x.strip() for x in re.split(r"[\s,;]+", raw) if x.strip()]
        save_blocklist(entries)
        msg, kind = f"Blocklist saved ({len(load_blocklist()['domains'])} domain(s)).", "success"

    bl = load_blocklist()
    domains = bl.get("domains", [])
    notes = bl.get("notes", {})
    listing = "".join(
        '<div style="display:flex;justify-content:space-between;padding:7px 12px;'
        'background:#f8fafc;border-radius:6px;margin-bottom:5px;font-size:13px">'
        f'<code>{e(dom)}</code><span class="text-muted" style="font-size:11.5px">{e(notes.get(dom,""))}</span></div>'
        for dom in domains
    ) or '<div class="panel-empty">Nothing blocked yet.</div>'

    body = (
        alert(msg, kind) +
        '<div class="card"><h3>&#128683; Never contact these domains</h3>'
        '<p style="font-size:13px;color:#64748b;line-height:1.6;margin-bottom:14px">'
        'Checked against both the company website and the address found on it, in every '
        'term. Subdomains are covered automatically — blocking <code>gridgear.ca</code> '
        'also blocks <code>careers.gridgear.ca</code> and <code>hr@mail.gridgear.ca</code>. '
        'Blocked companies are dropped before any scraping happens, so they cost nothing.</p>'
        '<form method="post">'
        '<div class="form-group"><label class="form-label">One domain per line</label>'
        f'<textarea name="domains" rows="8" spellcheck="false">{e(chr(10).join(domains))}</textarea>'
        '<div class="form-hint">Paste a full URL or an email address if that\'s easier — '
        'it gets reduced to the domain automatically.</div></div>'
        '<button class="btn btn-primary">&#9989; Save blocklist</button></form></div>'
        f'<div class="card"><h3>Currently blocked ({len(domains)})</h3>{listing}</div>'
    )
    return layout("Blocklist", "blocklist", body, cfg)


# ══════════════════════════════════════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_cfg()
    msg, kind = "", ""
    if request.method == "POST":
        if request.form.get("action") == "clear_cache":
            msg, kind = f"Cleared {clear_cache()} cached site(s).", "success"
        else:
            for key in ("internship_term", "your_name", "portfolio_url", "location", "default_focus"):
                cfg[key] = request.form.get(key, cfg.get(key, "")).strip()
            try:
                cfg["daily_limit"] = max(1, int(request.form.get("daily_limit", "20")))
            except ValueError:
                pass
            save_cfg(cfg)
            msg, kind = "Settings saved.", "success"
            cfg = load_cfg()

    warn = ""
    if cfg.get("daily_limit", 20) > 50:
        warn = alert(
            f"Daily limit is set to {cfg['daily_limit']}. Gmail's own cap is around 500/day, "
            "but sending anywhere near that volume of unsolicited mail from a personal account "
            "is a good way to get it rate-limited or suspended, and to land in spam folders. "
            "Something in the 15–30 range is far safer.", "warn")

    field = lambda name, label, hint="": (
        f'<div class="form-group"><label class="form-label">{label}</label>'
        f'<input type="text" name="{name}" value="{e(cfg.get(name,""))}">'
        + (f'<div class="form-hint">{hint}</div>' if hint else '') + '</div>'
    )

    body = (
        alert(msg, kind) + warn +
        '<div class="card"><h3>&#9881; Settings</h3><form method="post">'
        + field("internship_term", "Co-op term",
                'e.g. "Winter 2027 (January - April)". Changing this makes every company '
                'eligible again — the blocklist still applies.')
        + field("your_name", "Your name")
        + field("location", "Location shown in the signature")
        + field("portfolio_url", "Portfolio URL")
        + field("default_focus", "Default focus",
                "Used when a company line has no focus column: power, pcb, robotics, "
                "semiconductor, embedded, test.")
        + '<div class="form-group"><label class="form-label">Daily email limit</label>'
        f'<input type="number" name="daily_limit" value="{cfg.get("daily_limit",20)}" min="1" max="500" style="width:110px">'
        '<div class="form-hint">Enforced on send and shown in the header — one value, one place.</div></div>'
        '<button class="btn btn-primary">&#9989; Save settings</button></form></div>'
        '<div class="card"><h3>&#128465; Scrape cache</h3>'
        '<p style="font-size:13px;color:#64748b;margin-bottom:12px">Discovered addresses are cached '
        f'per domain for {core.CACHE_TTL_DAYS} days so repeat runs skip the network entirely. '
        'Clear it if a company has changed its contact page.</p>'
        '<form method="post"><button class="btn btn-secondary" name="action" value="clear_cache">Clear cache</button>'
        '</form></div>'
    )
    return layout("Settings", "settings", body, cfg)


# ══════════════════════════════════════════════════════════════════════════════
# Map
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/map")
def map_view():
    cfg = load_cfg()
    head = ('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>'
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>')
    body = (
        '<div class="map-wrap"><div class="map-panel"><div class="map-panel-top">'
        '<div style="font-size:15px;font-weight:700;margin-bottom:10px">&#128506; Map Search</div>'
        '<div class="form-hint" style="margin-bottom:10px;background:#fef3c7;color:#92400e;'
        'padding:8px 10px;border-radius:6px;font-size:11px">&#9888; OpenStreetMap data — coverage '
        'is patchy. Click the map to add a company manually.</div>'
        '<div class="form-group"><label class="form-label">City</label>'
        '<div style="display:flex;gap:6px">'
        '<input id="city-input" type="text" placeholder="e.g. Waterloo, ON" style="flex:1" '
        'onkeydown="if(event.key===\'Enter\')geocodeCity()">'
        '<button class="btn btn-primary btn-sm" onclick="geocodeCity()" id="geo-btn">Go</button></div></div>'
        '<div class="form-group"><label class="form-label">Find companies</label>'
        '<div style="display:flex;gap:6px">'
        '<input id="query-input" type="text" placeholder="e.g. power electronics" style="flex:1" '
        'onkeydown="if(event.key===\'Enter\')searchCompanies()">'
        '<button class="btn btn-primary btn-sm" onclick="searchCompanies()" id="find-btn">&#128269;</button></div>'
        '<div class="form-hint" style="margin-top:4px">Leave blank to scan all offices near the city.</div></div>'
        '<div id="map-msg"></div></div>'
        '<div class="map-panel-scroll">'
        '<div class="panel-section-title">Companies found</div>'
        '<div id="results-list"><div class="panel-empty">Search a city to see companies.</div></div>'
        '<div class="panel-section-title" style="margin-top:16px">Send queue '
        '<span id="queue-count" style="background:#dcfce7;color:#166534;border-radius:20px;'
        'padding:1px 8px;font-size:10px;margin-left:6px;font-weight:700">0</span></div>'
        '<div id="draft-queue"><div class="panel-empty">No emails queued.</div></div>'
        '<div id="queue-actions" style="display:none;margin-top:10px">'
        '<button class="btn btn-success" style="width:100%;margin-bottom:6px" onclick="sendAllQueued()">'
        '&#9993; Send all queued</button>'
        '<button class="btn btn-secondary" style="width:100%" onclick="addAllToBulkDrafts()">'
        '&#128203; Move to bulk preview</button></div>'
        '</div></div><div id="leaflet-map"></div></div>'
        "<script>" + MAP_JS + "</script>"
    )
    return layout("Map Search", "map", body, cfg, head=head)


@app.route("/api/geocode", methods=["POST"])
def api_geocode():
    city = (request.json or {}).get("city", "").strip()
    if not city:
        return jsonify({"error": "No city provided."})
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": city, "format": "json", "limit": 1},
                         headers={"User-Agent": "AutoSend-EE-Outreach/3.0"}, timeout=10)
        data = r.json()
        if not data:
            return jsonify({"error": f'City "{city}" not found.'})
        top = data[0]
        return jsonify({"lat": float(top["lat"]), "lon": float(top["lon"]),
                        "display_name": top.get("display_name", city).split(",")[0]})
    except Exception as ex:
        return jsonify({"error": f"Geocoding error: {ex}"})


@app.route("/api/company_search", methods=["POST"])
def api_company_search():
    data = request.json or {}
    query = data.get("query", "").strip()
    try:
        lat, lon = float(data.get("lat", 0)), float(data.get("lon", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Bad coordinates."})

    kw = ""
    if query:
        safe = re.sub(r"[^a-zA-Z0-9 ]", "", query)
        kw = '["name"~"' + safe.replace(" ", "|") + '",i]'
    overpass = (
        "[out:json][timeout:28];\n(\n"
        f'  nwr["office"]{kw}(around:25000,{lat},{lon});\n'
        '  nwr["name"~"electric|power|hardware|semiconductor|robotics|engineering|tech|circuit|'
        'energy|systems|embedded|firmware|pcb|motor|control|automation|sensor|photonics|optic|'
        'quantum|aerospace|defence|defense",i]'
        '["amenity"!="restaurant"]["amenity"!="cafe"]["amenity"!="bar"]["amenity"!="fast_food"]'
        f"(around:20000,{lat},{lon});\n);\nout center tags;\n"
    )
    companies, seen = [], set()
    try:
        r = requests.post("https://overpass-api.de/api/interpreter",
                          data={"data": overpass}, timeout=30)
        for el in r.json().get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "").strip()
            if not name or len(name) < 2 or name.lower() in seen or len(companies) >= 80:
                continue
            centre = el if el.get("type") == "node" else el.get("center", {})
            c_lat, c_lon = centre.get("lat"), centre.get("lon")
            if not c_lat or not c_lon:
                continue
            website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
            if website and blocked_reason(website):
                continue                      # blocklist applies to the map too
            seen.add(name.lower())
            companies.append({
                "name": name, "lat": float(c_lat), "lon": float(c_lon),
                "website": website,
                "address": ", ".join(filter(None, [tags.get("addr:street", ""),
                                                   tags.get("addr:city", "")])),
            })
    except Exception:
        pass
    return jsonify({"companies": companies})


@app.route("/api/quick_draft", methods=["POST"])
def api_quick_draft():
    data = request.json or {}
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."})
    if not url.startswith("http"):
        url = "https://" + url

    hit = blocked_reason(url, name)
    if hit:
        return jsonify({"error": f"{hit} is on your blocklist."})

    cfg = load_cfg()
    dom = domain_of(url)
    pick = best_email(find_emails(url), dom)
    if not pick:
        return jsonify({"error": "No usable address found on that site."})
    if blocked_reason(pick):
        return jsonify({"error": f"{domain_of(pick)} is on your blocklist."})
    return jsonify({"draft": {
        "company": name, "email": pick,
        "subject": make_subject(name, cfg),
        "body": make_body(name, focus_hook(data.get("focus", ""), cfg), cfg),
        "url": url,
    }})


@app.route("/api/send_single", methods=["POST"])
def api_send_single():
    data = request.json or {}
    cfg = load_cfg()
    to = data.get("email", "")
    hit = blocked_reason(to, data.get("url", ""))
    if hit:
        return jsonify({"ok": False, "error": f"{hit} is on your blocklist."})
    if count_sent_today(log_rows()) >= daily_limit():
        return jsonify({"ok": False, "error": "Daily limit reached."})

    ok, err = send_one(to, data.get("subject", ""), data.get("body", ""))
    append_log([make_log_row(cfg, {
        "company": data.get("name", ""), "email": to,
        "subject": data.get("subject", ""), "url": data.get("url", ""),
    }, "SENT" if ok else "FAILED")])
    invalidate_log()
    return jsonify({"ok": ok, "error": "" if ok else err})


@app.route("/api/add_to_session_drafts", methods=["POST"])
def api_add_to_session_drafts():
    incoming = (request.json or {}).get("drafts", [])
    existing = drafts()
    seen = {d.get("email", "").lower() for d in existing}
    for item in incoming:
        d = item.get("draft") or item
        addr = d.get("email", "").lower()
        if addr and addr not in seen and not blocked_reason(addr):
            existing.append(d)
            seen.add(addr)
    set_drafts(existing)
    return jsonify({"ok": True, "total": len(existing)})


@app.route("/api/add_to_paste_list", methods=["POST"])
def api_add_to_paste_list():
    line = (request.json or {}).get("line", "").strip()
    pending = session.get("pending_companies", "")
    if line and line not in pending:
        session["pending_companies"] = (pending + "\n" + line).strip()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port,
            debug=os.environ.get("FLASK_ENV") != "production")

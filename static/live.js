// Live data from the server's stream (1.29) — shared by the Tactical and the Light page.
//
// The stream (/api/stream) sends what changed in the marks (mk), the alerts (st) and the feed (fd), instead of
// every open page downloading all three again after each post. Each part a page holds is labelled with `h`, the
// hash of the server's version of it (the full answers carry it too). A change says which version it starts
// from (`b`): a page holding exactly that applies it; a page holding anything else fetches the part in full.
// Lists: `set` = changed entries (same order), `pre` + `n` = new ones at the head, `ids` = a new order, `all` =
// the whole list. State: `sub`/`unset` = keys of a dict, `set` = whole values, `act` = the active alerts list.
// Nothing here guesses: a change that does not fit what the page holds is never patched in.
(function(){
  var alertId=function(a){ return a.location_uid+'|'+a.alert_type; };
  var idOf={mk:function(m){ return String(m.id); }, fd:function(p){ return p.post_id; }};
  function list(cur,d,idf){
    if(d.all) return d.all.slice();
    cur=cur||[];
    var by=new Map(); cur.forEach(function(x){ by.set(idf(x),x); });
    (d.set||[]).forEach(function(x){ by.set(idf(x),x); });
    var ids;
    if(d.ids) ids=d.ids;
    else if(d.pre){ d.pre.forEach(function(x){ by.set(idf(x),x); });
      ids=d.pre.map(idf).concat(cur.map(idf)).slice(0,d.n); if(ids.length!==d.n) return null; }
    else ids=cur.map(idf);
    var out=[];
    for(var i=0;i<ids.length;i++){ if(!by.has(ids[i])) return null; out.push(by.get(ids[i])); }
    return out;
  }
  function state(cur,d){
    if(d.all) return Object.assign({},d.all);
    if(!cur) return null;
    var s=Object.assign({},cur,d.set||{}), k;
    for(k in (d.sub||{})) s[k]=Object.assign({},cur[k]||{},d.sub[k]);
    for(k in (d.unset||{})){ s[k]=Object.assign({},s[k]||{}); d.unset[k].forEach(function(x){ delete s[k][x]; }); }
    if(d.act){ var a=list(cur.active||[],d.act,alertId); if(!a) return null; s.active=a; }
    return s;
  }
  // one part of a message: 'same' (nothing to do), {v: the new value}, or null (fetch it in full)
  function part(name,d,held,cur){
    if(!d||held===d.h) return 'same';
    if(!d.all&&held!==d.b) return null;
    var v=name==='st'?state(cur,d):list(cur,d,idOf[name]);
    return v?{v:v}:null;
  }
  var api={part:part,list:list,state:state,alertId:alertId};
  if(typeof window!=='undefined') window.LiveSync=api;
  if(typeof module!=='undefined') module.exports=api;
})();

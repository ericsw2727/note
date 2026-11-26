/* Canvas player for original PNG atlases. No edits to raster assets. */
(() => {
  const entries = window.ANIMATIONS;
  const cards = document.getElementById('cards');
  const stage = document.getElementById('stage');
  const speed = document.getElementById('speed');
  const status = document.getElementById('status');
  const images = new Map();
  let playing = true, frame = 0, selected = 0, last = null, elapsed = 0, travel = true;
  const canvases = [];
  const stats = { loaded: 0, ticks: 0, draws: 0, errors: [] };
  window.animationPreviewStats = stats;
  function draw(ctx, character, index, cx, baseline, size, mirror = false) {
    const f = character.frames[index], image = images.get(character.id);
    if (!image || !image.complete || !image.naturalWidth) return;
    const [sx,sy,sw,sh] = f.rect, [x0,y0,x1,y1] = f.visibleBounds;
    const scale = size / Math.max(...character.maxVisibleSize);
    const center = (x0+x1)/2;
    const rise = character.bob * [0,0.4,1,0.4][index] * size/96;
    ctx.save(); ctx.translate(cx, baseline-rise); if (mirror) ctx.scale(-1,1);
    ctx.drawImage(image,sx,sy,sw,sh,-center*scale,-y1*scale,sw*scale,sh*scale);
    ctx.restore(); stats.draws++;
  }
  function render() {
    entries.forEach((entry,i) => { const ctx=canvases[i].getContext('2d');ctx.clearRect(0,0,200,160);draw(ctx,entry,frame,100,146,124); });
    const ctx=stage.getContext('2d');ctx.clearRect(0,0,960,160);
    const distance=travel ? (elapsed*0.04)%1500 : 600;
    const reverse=distance>750;
    const x=travel ? 100+(reverse ? 1500-distance : distance) : 480;
    draw(ctx,entries[selected],frame,x,145,96,reverse);
    document.getElementById('frame').textContent=`${frame+1}/4`;
    document.getElementById('selected').textContent=entries[selected].name+' · '+entries[selected].motion;
  }
  document.getElementById('pause').onclick = event => {playing=!playing;event.target.textContent=playing?'일시정지':'재생';last=null;};
  document.getElementById('step').onclick = () => {playing=false;document.getElementById('pause').textContent='재생';frame=(frame+1)%4;render();};
  document.getElementById('background').onclick = () => document.body.classList.toggle('light');
  document.getElementById('travel').onchange = event => {travel=event.target.checked;render();};
  entries.forEach((entry,i) => {
    const card=document.createElement('button');card.className='card'+(i===0?' selected':'');
    const canvas=document.createElement('canvas');canvas.width=200;canvas.height=160;canvases.push(canvas);
    const title=document.createElement('strong');title.textContent=entry.name;
    const label=document.createElement('span');label.textContent=entry.motion;
    card.append(canvas,title,label);
    card.onclick=()=>{selected=i;document.querySelectorAll('.card').forEach(c=>c.classList.remove('selected'));card.classList.add('selected');render();};
    cards.append(card);
  });
  Promise.all(entries.map(entry=>new Promise((resolve,reject)=>{
    const image=new Image();images.set(entry.id,image);
    image.onload=()=>{stats.loaded++;resolve();};image.onerror=()=>reject(new Error(entry.image));image.src=entry.image;
  }))).then(()=>{
    status.textContent='12종 · 48프레임 로딩 완료';document.body.dataset.ready='true';render();
    function tick(t){stats.ticks++;if(last===null)last=t;const dt=t-last;last=t;if(playing){elapsed+=dt;const next=Math.floor(elapsed*Number(speed.value)/1000)%4;if(next!==frame){frame=next;}render();}requestAnimationFrame(tick);}
    requestAnimationFrame(tick);
  }).catch(error=>{stats.errors.push(error.message);status.textContent='불러오기 실패: '+error.message;});
})();

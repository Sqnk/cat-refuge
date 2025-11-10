(function(){
  const box = document.getElementById('searchBox');
  const list = document.getElementById('searchResults');
  if(!box || !list) return;

  let all = [];
  async function loadAll(){
    const res = await fetch('/api/cats');
    all = await res.json();
    render(all);
  }
  function render(items){
    list.innerHTML = '';
    if(items.length===0){ list.innerHTML = '<div class="list-group-item">Aucun résultat</div>'; return; }
    items.forEach(c=>{
      const a = document.createElement('a');
      a.href = '/chats/'+c.id;
      a.className = 'list-group-item list-group-item-action d-flex align-items-center';
      a.innerHTML = (c.photo ? '<img src="'+c.photo+'" style="height:28px;width:28px;object-fit:cover" class="me-2 rounded-circle">' : '') +
                    '<div><strong>'+c.name+'</strong><div class="small text-muted">'+c.age+'</div></div>';
      list.appendChild(a);
    });
  }
  box.addEventListener('input', ()=>{
    const q = box.value.toLowerCase();
    if(!q) { render(all); return; }
    const filtered = all.filter(c => c.name.toLowerCase().includes(q));
    render(filtered);
  });

  loadAll(); // liste complète au chargement
})();

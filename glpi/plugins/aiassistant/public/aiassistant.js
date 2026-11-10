/**
 * Bulle « Assistant IA » dans la chronologie du ticket et panneau des sources.
 *
 * La page interroge le proxy PHP (la clé d'API ne quitte jamais le serveur) :
 * 1 s pendant une réponse, 3 s au repos, en pause quand l'onglet est caché.
 */
(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  var isTicketPage = /\/ticket\.form\.php$/.test(window.location.pathname);
  var ticketId = parseInt(params.get('id') || '', 10);
  if (!isTicketPage || !(ticketId > 0)) {
    return;
  }

  var ACTIVE_MS = 1000;
  var IDLE_MS = 3000;
  var BUBBLE_ID = 'aiassistant-progress';
  var timer = null;
  var sawActive = false;

  function endpoint() {
    var root = (window.CFG_GLPI && window.CFG_GLPI.root_doc) || '';
    return root + '/plugins/aiassistant/ajax/ticket.php?id=' + ticketId;
  }

  function schedule(delay) {
    window.clearTimeout(timer);
    timer = window.setTimeout(poll, delay);
  }

  function poll() {
    if (document.hidden) {
      schedule(IDLE_MS);
      return;
    }
    // GLPI renvoie la page de login (HTML) à un visiteur sans session au lieu
    // du JSON 403 : on demande explicitement du JSON et on traite tout ce qui
    // n'en est pas comme « aucune donnée ».
    fetch(endpoint(), {
      headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'same-origin'
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error('HTTP ' + response.status);
        }
        return response.json();
      })
      .then(handle)
      .catch(function () {
        schedule(IDLE_MS);
      });
  }

  function handle(state) {
    if (!state || typeof state !== 'object' || !document.querySelector('.itil-timeline')) {
      schedule(IDLE_MS);
      return;
    }
    applySources(state.answers || []);

    if (state.status === 'thinking' || state.status === 'searching') {
      sawActive = true;
      renderBubble(state);
      schedule(ACTIVE_MS);
      return;
    }
    if (state.status === 'failed') {
      renderFailed();
      return;
    }
    if (sawActive && state.status === 'done') {
      window.location.reload();
      return;
    }
    removeBubble();
    schedule(IDLE_MS);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text) {
      node.textContent = text;
    }
    return node;
  }

  function favicon(source) {
    var letter = (source.title || source.domain || '?').charAt(0);
    if (!source.domain) {
      return el('span', 'aiassistant-favicon aiassistant-favicon--letter', letter);
    }
    var img = document.createElement('img');
    img.className = 'aiassistant-favicon';
    img.alt = '';
    img.loading = 'lazy';
    img.src = 'https://' + source.domain + '/favicon.ico';
    img.addEventListener('error', function () {
      img.replaceWith(el('span', 'aiassistant-favicon aiassistant-favicon--letter', letter));
    });
    return img;
  }

  function stack(sources) {
    var wrapper = el('span', 'aiassistant-stack');
    sources.slice(0, 3).forEach(function (source) {
      wrapper.appendChild(favicon(source));
    });
    return wrapper;
  }

  function count(sources) {
    return sources.length + (sources.length > 1 ? ' sources' : ' source');
  }

  function removeBubble() {
    var bubble = document.getElementById(BUBBLE_ID);
    if (bubble) {
      bubble.remove();
    }
  }

  function bubble() {
    var timeline = document.querySelector('.itil-timeline');
    if (!timeline) {
      return null;
    }
    var existing = document.getElementById(BUBBLE_ID);
    if (existing) {
      return existing;
    }
    var created = el('div', 'timeline-item aiassistant-bubble');
    created.id = BUBBLE_ID;
    timeline.appendChild(created);
    return created;
  }

  function renderBubble(state) {
    var host = bubble();
    if (!host) {
      return;
    }

    var body = el('div', 'aiassistant-steps');
    var steps = (state.steps || []).filter(function (step) {
      return step.kind === 'search';
    });
    var last = (state.steps || []).slice(-1)[0];
    var waiting = !last || last.kind === 'thinking';

    steps.forEach(function (step, index) {
      var row = el('div', 'aiassistant-step');
      if (!waiting && index === steps.length - 1) {
        row.className = 'aiassistant-step aiassistant-step--current';
        row.appendChild(el('span', 'aiassistant-spinner'));
      } else {
        row.className = 'aiassistant-step aiassistant-step--past';
      }
      row.appendChild(el('span', 'aiassistant-label', 'Recherche sur le web « ' + step.query + ' »'));
      if (step.sources && step.sources.length) {
        row.appendChild(stack(step.sources));
        row.appendChild(el('span', 'aiassistant-count', count(step.sources)));
      }
      body.appendChild(row);
    });

    if (waiting) {
      var thinking = el('div', 'aiassistant-step aiassistant-step--current');
      thinking.appendChild(el('span', 'aiassistant-spinner'));
      thinking.appendChild(el('span', 'aiassistant-label', 'Réflexion'));
      body.appendChild(thinking);
    }

    host.replaceChildren(el('div', 'aiassistant-title', 'Assistant IA'), body);
  }

  function renderFailed() {
    var host = bubble();
    if (!host) {
      return;
    }
    host.replaceChildren(
      el('div', 'aiassistant-title', 'Assistant IA'),
      el('div', 'aiassistant-failed', "L'assistant n'a pas pu répondre, un technicien prendra le relais.")
    );
  }

  function sourcesBlock(item) {
    var paragraphs = item.querySelectorAll('.read-only-content p');
    for (var i = 0; i < paragraphs.length; i += 1) {
      if ((paragraphs[i].textContent || '').trim().indexOf('Sources :') === 0) {
        return paragraphs[i];
      }
    }
    return null;
  }

  function panel(sources) {
    var body = el('div', 'aiassistant-panel');
    body.hidden = true;
    sources.forEach(function (source) {
      var link = el('a', 'aiassistant-source');
      link.appendChild(favicon(source));
      link.appendChild(el('span', 'aiassistant-source-title', source.title || source.url || ''));
      link.appendChild(el('span', 'aiassistant-source-domain', source.domain || ''));
      if (/^https?:\/\//i.test(source.url || '')) {
        link.href = source.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
      body.appendChild(link);
    });
    return body;
  }

  function applySources(answers) {
    answers.forEach(function (answer) {
      if (!answer.sources || !answer.sources.length) {
        return;
      }
      var item = document.querySelector(
        '.timeline-item[data-itemtype="ITILFollowup"][data-items-id="' + answer.followup_id + '"]'
      );
      if (!item || item.querySelector('.aiassistant-sources')) {
        return;
      }
      var host = item.querySelector('.read-only-content') || item;
      var block = sourcesBlock(item);
      if (block) {
        block.remove();
      }

      var wrapper = el('div', 'aiassistant-sources');
      var button = el('button', 'aiassistant-pill');
      button.type = 'button';
      button.setAttribute('aria-expanded', 'false');
      button.appendChild(stack(answer.sources));
      button.appendChild(el('span', 'aiassistant-count', count(answer.sources)));

      var body = panel(answer.sources);
      button.addEventListener('click', function (event) {
        event.stopPropagation();
        var open = body.hidden;
        body.hidden = !open;
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
      });

      wrapper.appendChild(button);
      wrapper.appendChild(body);
      host.appendChild(wrapper);
    });
  }

  function closePanels() {
    document.querySelectorAll('.aiassistant-panel:not([hidden])').forEach(function (body) {
      body.hidden = true;
    });
  }

  document.addEventListener('click', closePanels);

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closePanels();
    }
  });

  poll();
})();

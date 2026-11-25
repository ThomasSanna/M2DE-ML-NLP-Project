/**
 * Bulle « Assistant IA » dans la chronologie du ticket, panneau des sources et,
 * sous la dernière réponse, l'avis du demandeur (résolu ou demande d'un agent).
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
  var PENDING = 'aiassistant-sources-pending';
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
        revealPendingSources();
        schedule(IDLE_MS);
      });
  }

  function handle(state) {
    if (!state || typeof state !== 'object' || !document.querySelector('.itil-timeline')) {
      revealPendingSources();
      schedule(IDLE_MS);
      return;
    }
    var active = state.status === 'thinking' || state.status === 'searching';
    applySources(state.answers || []);
    applyVerdict(state.answers || [], Boolean(state.can_verdict) && !active);
    revealPendingSources();

    if (active) {
      sawActive = true;
      renderBubble(state);
      schedule(ACTIVE_MS);
      return;
    }
    if (state.status === 'failed') {
      // The worker retries the event: keep polling so a retry shows up.
      renderFailed();
      schedule(IDLE_MS);
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
      var item = followupItem(answer.followup_id);
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

  var VERDICT_TEXT = {
    resolved: 'Le demandeur indique que cette réponse a résolu son problème.',
    technician: "Le demandeur a demandé l'intervention d'un technicien."
  };

  function followupItem(followupId) {
    return document.querySelector(
      '.timeline-item[data-itemtype="ITILFollowup"][data-items-id="' + followupId + '"]'
    );
  }

  // Everyone sees the verdict (the technician decides from it to close the ticket); only a
  // requester gets the buttons, on the latest answer, until the ticket is handed over.
  function applyVerdict(answers, canVerdict) {
    var latest = answers[answers.length - 1];
    var handedOver = answers.some(function (answer) {
      return answer.escalated || answer.verdict === 'technician';
    });
    document.querySelectorAll('.aiassistant-verdict--ask').forEach(function (node) {
      if (!canVerdict || handedOver || !latest || node.dataset.followupId !== String(latest.followup_id)) {
        node.remove();
      }
    });
    answers.forEach(function (answer) {
      var item = VERDICT_TEXT[answer.verdict] && followupItem(answer.followup_id);
      if (item && !item.querySelector('.aiassistant-verdict--given')) {
        var host = item.querySelector('.read-only-content') || item;
        host.appendChild(el('div', 'aiassistant-verdict aiassistant-verdict--given', VERDICT_TEXT[answer.verdict]));
      }
    });
    if (!canVerdict || handedOver || !latest || latest.verdict) {
      return;
    }
    var item = followupItem(latest.followup_id);
    if (!item || item.querySelector('.aiassistant-verdict--ask')) {
      return;
    }
    (item.querySelector('.read-only-content') || item).appendChild(verdictButtons(latest.followup_id));
  }

  function verdictButtons(followupId) {
    var wrapper = el('div', 'aiassistant-verdict aiassistant-verdict--ask');
    wrapper.dataset.followupId = String(followupId);
    var actions = el('div', 'aiassistant-verdict-actions');
    var error = el('div', 'aiassistant-verdict-error');
    error.hidden = true;
    var buttons = [
      verdictButton('resolved', "Oui, c'est résolu", 'btn btn-sm btn-success'),
      verdictButton('technician', 'Demander à un agent', 'btn btn-sm btn-outline-secondary')
    ];
    buttons.forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.stopPropagation();
        sendVerdict(button.dataset.verdict, buttons, error);
      });
      actions.appendChild(button);
    });
    wrapper.appendChild(el('span', 'aiassistant-verdict-question', 'Cette réponse a-t-elle résolu votre problème ?'));
    wrapper.appendChild(actions);
    wrapper.appendChild(error);
    return wrapper;
  }

  function verdictButton(verdict, label, className) {
    var button = el('button', className, label);
    button.type = 'button';
    button.dataset.verdict = verdict;
    return button;
  }

  function sendVerdict(verdict, buttons, error) {
    var token = document.querySelector('meta[property="glpi:csrf_token"]');
    buttons.forEach(function (button) {
      button.disabled = true;
    });
    error.hidden = true;
    fetch(endpoint(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Requested-With': 'XMLHttpRequest',
        'X-Glpi-Csrf-Token': token ? token.getAttribute('content') : ''
      },
      credentials: 'same-origin',
      body: new URLSearchParams({ verdict: verdict }).toString()
    })
      .then(function (response) {
        // 409: the answer no longer awaits a verdict (another tab, a newer answer).
        if (response.status !== 204 && response.status !== 409) {
          throw new Error('HTTP ' + response.status);
        }
        // The note and the new status are in the timeline only after a reload.
        window.location.reload();
      })
      .catch(function () {
        buttons.forEach(function (button) {
          button.disabled = false;
        });
        error.textContent = "Votre réponse n'a pas pu être enregistrée, réessayez.";
        error.hidden = false;
      });
  }

  function closePanels() {
    document.querySelectorAll('.aiassistant-panel:not([hidden])').forEach(function (body) {
      body.hidden = true;
    });
    document.querySelectorAll('.aiassistant-pill[aria-expanded="true"]').forEach(function (pill) {
      pill.setAttribute('aria-expanded', 'false');
    });
  }

  function isSourcesParagraph(node) {
    return (node.textContent || '').trim().indexOf('Sources :') === 0;
  }

  // The plain-text list must not show before the pill replaces it. The timeline arrives
  // through AJAX: the observer hides each list as it is inserted, before the next paint.
  function hidePendingSources(root) {
    if (root.matches && root.matches('.read-only-content p') && isSourcesParagraph(root)) {
      root.classList.add(PENDING);
    }
    if (!root.querySelectorAll) {
      return;
    }
    root.querySelectorAll('.read-only-content p').forEach(function (paragraph) {
      if (isSourcesParagraph(paragraph)) {
        paragraph.classList.add(PENDING);
      }
    });
  }

  // A list with no stored sources (older answer, API down) stays readable as text.
  function revealPendingSources() {
    document.querySelectorAll('.' + PENDING).forEach(function (paragraph) {
      paragraph.classList.remove(PENDING);
    });
  }

  hidePendingSources(document);
  new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      mutation.addedNodes.forEach(hidePendingSources);
    });
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('click', closePanels);

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closePanels();
    }
  });

  poll();
})();

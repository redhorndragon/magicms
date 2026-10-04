/** 免刷新的掌握状态标记：三态按钮直接设定、收藏。 */
(function () {
  'use strict';

  var STATUS_LABEL = { unknown: '不认识', learning: '学习中', mastered: '已掌握' };

  function request(url, options) {
    return fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options))
      .then(function (r) { return r.json(); });
  }

  function post(url, body) {
    return request(url, { method: 'POST', body: JSON.stringify(body || {}) });
  }

  /** 把某个单词的状态同步到页面：三态按钮高亮 + 卡片色条 + 复习卡角标 */
  function applyStatus(wordid, status) {
    var groups = document.querySelectorAll('.status-group[data-wordid="' + wordid + '"]');
    Array.prototype.forEach.call(groups, function (group) {
      var opts = group.querySelectorAll('[data-status]');
      Array.prototype.forEach.call(opts, function (btn) {
        btn.classList.toggle('on', btn.getAttribute('data-status') === status);
      });
    });

    var card = document.querySelector(
      '.word-card[data-wordid="' + wordid + '"], .flip-card[data-wordid="' + wordid + '"]');
    if (card) {
      card.classList.remove('status-unknown', 'status-learning', 'status-mastered');
      card.classList.add('status-' + status);
      var corner = card.querySelector('.status-corner');
      if (corner) {
        corner.innerHTML = '<i class="dot dot-' + status + '"></i>' + (STATUS_LABEL[status] || status);
      }
    }
    return card;
  }

  function refreshChips() {
    fetch('/api/stats' + location.search.replace(/^\?/, '?').replace('?', '?'))
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        if (!payload.ok) { return; }
        var s = payload.stats;
        var pairs = [['chip-unknown', '不认识 ' + s.unknown],
                     ['chip-learning', '学习中 ' + s.learning],
                     ['chip-mastered', '已掌握 ' + s.mastered]];
        pairs.forEach(function (pair) {
          var el = document.querySelector('.' + pair[0]);
          if (el) { el.textContent = pair[1]; }
        });
      })
      .catch(function () { /* 统计刷新失败不影响主流程 */ });
  }

  function pulse(el) {
    el.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.06)' }, { transform: 'scale(1)' }],
      { duration: 220, easing: 'ease-out' });
    setTimeout(refreshChips, 260);
  }

  /** 局部刷新右侧内容区：只替换 .content-col，左侧词根列表保持不动 */
  function loadWordsBody(url, push) {
    var col = document.querySelector('.content-col');
    if (!col) { window.location.href = url; return; }
    var sep = url.indexOf('?') === -1 ? '?' : '&';
    fetch(url + sep + 'partial=1', { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        col.innerHTML = html;
        if (push) { history.pushState(null, '', url); }
      })
      .catch(function () { window.location.href = url; });  // 失败则整页跳转兜底
  }

  /** 左侧词根列表不在局部刷新的响应里，需手动同步高亮 */
  function syncSideActive(link) {
    var actives = document.querySelectorAll('.side-item.active');
    Array.prototype.forEach.call(actives, function (el) { el.classList.remove('active'); });
    if (link.classList.contains('side-item')) { link.classList.add('active'); }
  }

  document.addEventListener('click', function (ev) {
    var target = ev.target;

    // 词根 / 筛选 / 翻页：只局部刷新右侧，避免左侧列表被重建（保留滚动位置与展开状态）
    var navLink = target.closest('a[data-nav="words"]');
    if (navLink) {
      ev.preventDefault();
      ev.stopPropagation();
      syncSideActive(navLink);
      loadWordsBody(navLink.getAttribute('href'), true);
      return;
    }

    // 收藏
    var starBtn = target.closest('[data-star]');
    if (starBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      var sid = starBtn.getAttribute('data-star');
      post('/api/word/' + sid + '/star').then(function (data) {
        if (!data.ok) { return; }
        document.querySelectorAll('[data-star="' + sid + '"]').forEach(function (btn) {
          btn.classList.toggle('on', !!data.starred);
        });
        pulse(starBtn);
      });
      return;
    }

    // 三态按钮：直接设定为「不认识 / 学习中 / 已掌握」
    var setBtn = target.closest('[data-set]');
    if (setBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      var wid = setBtn.getAttribute('data-set');
      var status = setBtn.getAttribute('data-status');
      var host = setBtn.closest('.word-card, .flip-card');
      post('/api/word/' + wid + '/status', { status: status }).then(function (data) {
        if (!data.ok) { return; }
        var card = applyStatus(wid, data.status);
        // 复习页只保留「不认识 / 学习中」，标记已掌握后移出
        if (data.status === 'mastered' && host && host.classList.contains('flip-card')) {
          host.classList.add('removing');
          setTimeout(function () { host.remove(); }, 420);
        } else {
          pulse(card || setBtn);
        }
      });
      return;
    }

    // 翻卡（点击按钮不触发翻面）
    var flipCard = target.closest('.flip-card');
    if (flipCard && !target.closest('button')) {
      flipCard.classList.toggle('flipped');
    }
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter') { return; }
    var card = document.activeElement && document.activeElement.closest('.flip-card');
    if (card) { card.classList.toggle('flipped'); }
  });

  // 前进/后退：按当前 URL 重新局部刷新，同样不重建左侧列表
  window.addEventListener('popstate', function () {
    loadWordsBody(location.href, false);
  });

  // 生成静态生词表：让后端执行 scripts/export_unknown.py（当前用户）
  Array.prototype.forEach.call(document.querySelectorAll('[data-export]'), function (btn) {
    btn.addEventListener('click', function () {
      if (btn.disabled) { return; }
      var original = btn.textContent;
      btn.disabled = true;
      btn.textContent = '生成中…';
      post('/api/export', {})
        .then(function (data) {
          if (!data.ok) {
            alert('生成失败：' + (data.error || '未知错误'));
            return;
          }
          btn.textContent = '已生成 ✓';
          btn.title = data.message || '';
          setTimeout(function () { btn.textContent = original; }, 2000);
        })
        .catch(function (err) { alert('生成失败：' + err); })
        .then(function () { btn.disabled = false; });
    });
  });

  // 切换用户（无密码）：跳到后端切换会话的路由，再回到当前页面
  var userSelect = document.getElementById('user-select');
  if (userSelect) {
    userSelect.addEventListener('change', function () {
      window.location.href = '/user/' + encodeURIComponent(userSelect.value);
    });
  }
})();

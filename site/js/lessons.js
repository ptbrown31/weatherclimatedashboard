/* The lessons, as the site's own copy of the two Traders' Academy courses.

   A video can show a page and a page can be opened, so what this carries that
   the recordings cannot is the link. Every lesson names the page it teaches
   and sends a reader straight there, and every lesson keeps its own address,
   so one can be pointed at from anywhere.

   The words are the courses' own, parsed from the scripts rather than retyped,
   so the two do not drift. Lessons fold shut because eighteen of them at full
   length is a wall, and the one named in the address opens itself. */
window.WXLessons = (function () {
  'use strict';
  const { el, txt, h, $ } = WXC;

  const esc = s => String(s == null ? '' : s);
  // the scripts carry a little inline markup for emphasis, which is the only
  // markup they carry, so it travels rather than being stripped
  const rich = (host, s) => { host.innerHTML = String(s || '')
    .replace(/<(?!\/?(b|i|em|strong)\b)[^>]*>/g, ''); };

  const slugOf = (course, n) => course.slug + '-' + n;

  function lessonEl(course, l) {
    const det = h('details', { class: 'lesson', id: slugOf(course, l.n) });
    const sum = h('summary');
    sum.appendChild(h('span', { class: 'lnum', text: String(l.n).padStart(2, '0') }));
    sum.appendChild(h('span', { class: 'ltitle', text: l.title }));
    sum.appendChild(h('span', { class: 'ldur', text: l.dur }));
    det.appendChild(sum);

    const body = h('div', { class: 'lbody' });
    const aim = h('p', { class: 'laim' });
    rich(aim, l.aim);
    body.appendChild(aim);

    if (l.link) {
      const a = h('a', { class: 'lgo', href: l.link.href });
      a.textContent = l.link.label + ' →';
      body.appendChild(a);
    }

    (l.parts || []).forEach(p => {
      body.appendChild(h('p', { class: 'lcue', text: p.cue }));
      (p.text || []).forEach(t => { const q = h('p'); rich(q, t); body.appendChild(q); });
      if (p.note) { const n = h('p', { class: 'lnote' }); rich(n, p.note); body.appendChild(n); }
    });

    if ((l.quiz || []).length) {
      const qz = h('div', { class: 'lquiz' });
      qz.appendChild(h('h3', { text: 'Check yourself' }));
      const ol = h('ol');
      l.quiz.forEach(q => {
        const li = h('li');
        const d = h('details');
        const s2 = h('summary'); rich(s2, q.q); d.appendChild(s2);
        const a = h('p', { class: 'lans' }); rich(a, q.a); d.appendChild(a);
        li.appendChild(d); ol.appendChild(li);
      });
      qz.appendChild(ol);
      body.appendChild(qz);
    }
    det.appendChild(body);
    return det;
  }

  function courseEl(course) {
    const sec = h('section', { class: 'course', id: course.slug });
    sec.appendChild(h('div', { class: 'secttl', text: course.title.toUpperCase() }));
    const meta = [course.level, course.lessons.length + ' lessons',
                  runtime(course) + ' of video'].join(' · ');
    sec.appendChild(h('p', { class: 'cmeta', text: meta }));
    const blurb = h('p', { class: 'cap', style: 'margin-top:0' });
    rich(blurb, course.blurb);
    sec.appendChild(blurb);
    course.lessons.forEach(l => sec.appendChild(lessonEl(course, l)));
    return sec;
  }

  function runtime(course) {
    const secs = course.lessons.reduce((a, l) => {
      const m = /(\d+):(\d+)/.exec(l.dur);
      return a + (m ? +m[1] * 60 + +m[2] : 0);
    }, 0);
    return Math.round(secs / 60) + ' minutes';
  }

  function openFromHash() {
    const wanted = (location.hash || '').replace('#', '');
    if (!wanted) return;
    const t = document.getElementById(wanted);
    if (!t || t.tagName !== 'DETAILS') return;
    t.open = true;
    t.scrollIntoView({ block: 'start' });
  }

  async function init() {
    const host = $('#lessons'); if (!host) return;
    let doc = null;
    try { doc = await fetch(WXC.asset('lessons.json')).then(r => r.json()); } catch (e) { doc = null; }
    if (!doc || !doc.courses) {
      host.appendChild(h('p', { class: 'cap', text: 'The lessons could not be loaded.' }));
      return;
    }
    doc.courses.forEach(c => host.appendChild(courseEl(c)));
    /* The address may name a lesson, in which case it opens and is scrolled
       to. Done after the lessons are on the page rather than while they are
       built, so it also serves a hash that arrives later, which is every
       link between two lessons and every use of the back button. The browser
       cannot do this itself because the lessons do not exist when it reads
       the fragment. */
    openFromHash();
    window.addEventListener('hashchange', openFromHash);
    // opening a lesson puts it in the address, so a reader can send it on
    host.addEventListener('toggle', ev => {
      const d = ev.target;
      if (d.tagName === 'DETAILS' && d.classList.contains('lesson') && d.open) {
        history.replaceState(null, '', '#' + d.id);
      }
    }, true);
  }

  return { init };
})();

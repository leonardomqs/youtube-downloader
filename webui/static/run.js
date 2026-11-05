/* O andamento de uma rodada, redesenhado enquanto ela acontece.
 *
 * A página não guarda estado: pergunta ao servidor de segundo em segundo e
 * reescreve o que mudou. Um `setTimeout` encadeado, e não `setInterval`, para
 * que uma resposta lenta não faça as consultas se empilharem.
 *
 * Tudo o que vem do servidor entra por `textContent`, nunca por `innerHTML`:
 * título de vídeo é texto de terceiro, e no YouTube cabe qualquer coisa.
 */
(function () {
  const root = document.getElementById("run");
  if (!root) return;

  const STATUS_URL = root.dataset.statusUrl;
  const STOP_URL = root.dataset.stopUrl;
  const INTERVAL = 1000;

  const el = (id) => document.getElementById(id);

  function row(parts) {
    const li = document.createElement("li");
    for (const [className, text] of parts) {
      if (text === "" || text === undefined) continue;
      const span = document.createElement("span");
      span.className = className;
      span.textContent = text;
      li.appendChild(span);
    }
    return li;
  }

  function fill(list, rows) {
    list.replaceChildren(...rows);
  }

  function draw(s) {
    const state = el("state");
    state.textContent = s.state_label;
    state.className = "badge badge--" + s.state;

    el("title").textContent = s.title || "—";
    el("phase").textContent = s.phase;
    el("position").textContent = s.current_link
      ? ` · link ${s.current_link} de ${s.total_links}`
      : "";
    // Numa live o total ainda não existe: uma porcentagem seria sempre zero.
    el("percent-box").hidden = s.open_ended;
    el("percent").textContent = s.percent;
    el("speed").textContent = s.speed;
    el("eta").textContent = s.eta ? "faltam " + s.eta : "";

    // Fora do download — convertendo, juntando, embutindo a capa — não existe
    // porcentagem: a faixa listrada diz "trabalhando" sem fingir um número.
    const bar = el("bar");
    const unknown = !s.is_over && s.phase !== "baixando" && s.current_link > 0;
    bar.classList.toggle("bar-fill--unknown", unknown);
    bar.style.width = s.is_over ? "100%" : s.percent + "%";

    fill(
      el("items"),
      s.items.map((i) =>
        row([
          ["item-position", i.position],
          ["item-title", i.title],
          ["item-target", i.target],
        ])
      )
    );
    el("items-empty").hidden = s.items.length > 0;

    el("failures-card").hidden = s.failures.length === 0;
    fill(
      el("failures"),
      s.failures.map((f) => row([["item-title item-error", f.reason]]))
    );

    el("summary-card").hidden = s.summary.length === 0 && !s.error;
    fill(
      el("summary"),
      s.summary.map((line) =>
        row([
          ["item-position", line.label],
          ["item-title", line.verdict],
          ["item-target", line.folder],
        ])
      )
    );
    el("error").textContent = s.error || "";

    const log = el("log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.textContent = s.lines.join("\n");
    // Só acompanha o fim se a pessoa já estava no fim: quem rolou para cima
    // está lendo alguma coisa.
    if (atBottom) log.scrollTop = log.scrollHeight;

    el("stop").hidden = s.is_over;

    // O andamento na aba, para quem deixa a página em segundo plano.
    document.title = s.is_over
      ? `${s.state_label} · youtube-downloader`
      : `${s.open_ended ? s.phase : Math.round(s.percent) + "%"} · ${s.title || "baixando"}`;
  }

  el("stop").addEventListener("click", async () => {
    el("stop").disabled = true;
    el("stop").textContent = "Parando…";
    try {
      const response = await fetch(STOP_URL, { method: "POST" });
      if (response.ok) draw(await response.json());
    } catch (error) {
      /* o próximo tique mostra o que aconteceu */
    }
  });

  // Abrir a pasta é uma ação do servidor local; sem JavaScript o formulário
  // faz o POST e volta com um aviso. Com JavaScript, não vale recarregar a
  // página inteira no meio de um download.
  for (const form of document.querySelectorAll(".open-folder")) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      fetch(form.action, { method: "POST" }).catch(() => {});
    });
  }

  async function tick() {
    try {
      const response = await fetch(STATUS_URL, { cache: "no-store" });
      if (response.ok) {
        const s = await response.json();
        draw(s);
        if (s.is_over) return;
      }
    } catch (error) {
      /* servidor reiniciando ou janela fechando: tenta de novo no próximo */
    }
    setTimeout(tick, INTERVAL);
  }

  tick();
})();

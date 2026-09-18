// The problems page: one screen for reviewing what the system found.
//
// # Why this is a page and not the banner
//
// The banner answers "is anything wrong right now", and it has to answer that in
// the corner of whatever the user was doing. That makes it the wrong place for
// everything else: filtering, comparing, reading the reasoning, deciding what to
// do. A surface that must be glanceable cannot also be a worklist, and trying to
// make it both produced the state this page replaces — 276 rows in a strip above
// the task input, which nobody could review and so nobody read.
//
// The two now have separate jobs. The banner says how many problems there are and
// links here; this page is where they are actually dealt with.
//
// # What it deliberately keeps from the banner
//
// The grouping, for one thing: this page never re-derives it, because a second
// definition of "the same problem" would disagree with the first the moment an id
// format changed. It renders `payload.groups` exactly as the server grouped it.
(function () {
  "use strict";

  const handlingFilters = [
    { id: "all", label: "全部" },
    { id: "human", label: "需要我判断" },
    { id: "approval", label: "需要我批准" },
    { id: "automatic", label: "系统可自行处理" },
  ];

  // The filter is held here rather than in the DOM so a re-render from polling
  // does not throw the reader back to "all" every few seconds — which is what the
  // banner does, and is acceptable there because it is not being read.
  let activeFilter = "all";
  let expandedIdentity = "";
  let lastPayload = null;

  function text(selector, value) {
    const node = document.querySelector(selector);
    if (node && node.textContent !== String(value)) node.textContent = String(value);
  }

  function handleLabels() {
    return (window.tangyingAlertLabels && window.tangyingAlertLabels.handling) || {};
  }

  function codeLabels() {
    return (window.tangyingAlertLabels && window.tangyingAlertLabels.codes) || {};
  }

  function handlingLabel(handling) {
    const entry = handleLabels()[String(handling || "")];
    return entry ? entry.text : String(handling || "");
  }

  function titleFor(group) {
    const labels = codeLabels();
    const code = String(group.code || "");
    return labels[code] || code || "系统发现了异常";
  }

  function severityLabel(severity) {
    const labels = (window.tangyingAlertLabels && window.tangyingAlertLabels.severities) || {};
    return labels[String(severity || "").toLowerCase()] || String(severity || "");
  }

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = String(content);
    return node;
  }

  // render draws the whole page from one payload.
  function render(payload) {
    const host = document.querySelector("#problems-list");
    if (!host) return;
    lastPayload = payload;

    const groups = (Array.isArray(payload && payload.groups) ? payload.groups : [])
      .filter((group) => group.active);
    const reports = Number((payload && payload.activeCount) || 0);

    renderSummary(groups, reports);
    renderFilters(groups);

    const shown = activeFilter === "all"
      ? groups
      : groups.filter((group) => String(group.handling) === activeFilter);

    host.replaceChildren();
    if (!groups.length) {
      host.append(emptyState(
        "当前没有未处理的问题",
        "监督 agent 正在运行；一旦发现问题会出现在这里，同时工作台顶部会提示。"));
      return;
    }
    if (!shown.length) {
      host.append(emptyState(
        "这个筛选下没有问题",
        "换个筛选条件看看；其他类别里还有 " + groups.length + " 个问题。"));
      return;
    }
    for (const group of shown) host.append(problemCard(group));
  }

  function renderSummary(groups, reports) {
    const byHandling = { human: 0, approval: 0, automatic: 0 };
    for (const group of groups) {
      const key = String(group.handling || "human");
      byHandling[key] = (byHandling[key] || 0) + 1;
    }
    text("#problems-headline", groups.length ? `${groups.length} 个问题` : "没有问题");
    // Both numbers, because their difference is itself the finding: a handful of
    // problems described by hundreds of reports is a system that is noisy, and
    // hiding either number would make this page look like it had lost data.
    text("#problems-subline", groups.length
      ? `${byHandling.human || 0} 个需要你判断 · ${byHandling.approval || 0} 个需要你批准 · `
        + `${byHandling.automatic || 0} 个系统可自行处理 · 共 ${reports} 条报告`
      : "监督 agent 没有发现需要处理的问题。");
  }

  function renderFilters(groups) {
    const host = document.querySelector("#problems-filters");
    if (!host) return;
    const counts = { all: groups.length, human: 0, approval: 0, automatic: 0 };
    for (const group of groups) {
      const key = String(group.handling || "human");
      counts[key] = (counts[key] || 0) + 1;
    }
    host.replaceChildren();
    for (const filter of handlingFilters) {
      const button = element("button", "problems-filter", `${filter.label} ${counts[filter.id] || 0}`);
      button.type = "button";
      button.dataset.filter = filter.id;
      button.setAttribute("aria-pressed", String(activeFilter === filter.id));
      // A filter with nothing behind it is disabled rather than hidden: hiding it
      // makes the set of categories change as problems come and go, and a reader
      // cannot learn where things go.
      button.disabled = (counts[filter.id] || 0) === 0;
      button.addEventListener("click", () => {
        activeFilter = filter.id;
        if (lastPayload) render(lastPayload);
      });
      host.append(button);
    }
  }

  function emptyState(title, detail) {
    const box = element("div", "problems-empty");
    box.append(element("strong", "", title));
    box.append(element("p", "", detail));
    return box;
  }

  function problemCard(group) {
    const identity = String(group.identity || "");
    const card = element("article", "problem-card");
    card.dataset.handling = String(group.handling || "human");
    card.dataset.severity = String(group.severity || "info").toLowerCase();

    const head = element("header", "problem-head");
    const heading = element("div", "problem-heading");
    heading.append(element("h3", "", titleFor(group)));
    heading.append(element("code", "problem-code", identity));
    head.append(heading);

    const badges = element("div", "problem-badges");
    badges.append(element("span", "problem-handling", handlingLabel(group.handling)));
    if (group.severity) badges.append(element("span", "problem-severity", severityLabel(group.severity)));
    head.append(badges);
    card.append(head);

    if (group.message) card.append(element("p", "problem-message", group.message));

    // Scope, on one line. "Affects 34 tasks" is what separates a decision from a
    // detail, and it is the number the flat list could never show.
    const scope = [];
    if (Number(group.taskCount) > 0) scope.push(`影响 ${group.taskCount} 个任务`);
    if (Number(group.count) > Number(group.taskCount)) scope.push(`共 ${group.count} 次报告`);
    if (group.firstSeen) {
      scope.push(`最早 ${new Date(group.firstSeen).toLocaleString()}`);
    }
    if (scope.length) card.append(element("p", "problem-scope", scope.join(" · ")));

    if (group.why) card.append(element("p", "problem-why", group.why));
    if (group.automaticRetryForbidden) {
      card.append(element("p", "problem-forbidden", "禁止自动重试：物理结果未知，必须先对账。"));
    }

    card.append(detailToggle(group, identity));
    const detail = renderDetail(group, identity);
    if (detail) card.append(detail);
    return card;
  }

  function detailToggle(group, identity) {
    const row = element("div", "problem-actions");
    const expanded = expandedIdentity === identity;
    const toggle = element("button", "secondary", expanded ? "收起详情" : "查看详情");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.addEventListener("click", () => {
      expandedIdentity = expanded ? "" : identity;
      if (lastPayload) render(lastPayload);
      // Keep the reader where they were: they clicked a button inside a card, and
      // a re-render that scrolled to the top would lose the card they were reading.
      const restored = document.querySelector(`[data-identity="${CSS.escape(identity)}"]`);
      if (restored) restored.scrollIntoView({ block: "nearest" });
    });
    row.append(toggle);
    return row;
  }

  // renderDetail shows the reasoning and the proposal — the two things a person
  // needs in order to disagree.
  function renderDetail(group, identity) {
    const expanded = expandedIdentity === identity;
    const box = element("div", "problem-detail");
    box.dataset.identity = identity;
    box.hidden = !expanded;
    if (!expanded) return box;

    const actions = group.recommendedActions || [];
    if (actions.length) {
      const advice = element("div", "problem-advice");
      advice.append(element("strong", "", "建议动作"));
      const list = document.createElement("ol");
      for (const action of actions) list.append(element("li", "", action));
      advice.append(list);
      box.append(advice);
    }

    if (group.missingEvidence && group.missingEvidence.length) {
      const gaps = element("div", "problem-missing");
      gaps.append(element("strong", "", "还缺什么"));
      for (const entry of group.missingEvidence) gaps.append(element("span", "", entry));
      box.append(gaps);
    }

    // The proposal, in the same shape the workspace shows it, so an operator does
    // not have to learn two readings of one plan.
    if (group.recovery) {
      const host = element("div", "problem-recovery");
      box.append(host);
      if (window.tangyingRenderRecovery) {
        const rendered = window.tangyingRenderRecovery({
          recovery: group.recovery,
          investigation: group.investigation,
          taskId: (group.tasks || [])[0] || "",
        });
        if (rendered) host.append(rendered);
      }
    }

    const tasks = group.tasks || [];
    if (tasks.length) {
      const row = element("div", "problem-tasks");
      row.append(element("strong", "", "相关任务"));
      for (const taskId of tasks) {
        const button = element("button", "problem-task-link", taskId);
        button.type = "button";
        button.addEventListener("click", () => {
          // The task id is the handle the workspace already uses. Carrying it
          // there is the whole interaction; a second way to open a task would be a
          // second place for the two to disagree.
          const field = document.querySelector("#local-task-id");
          if (field) {
            field.value = taskId;
            location.hash = "#tasks";
            field.focus();
          }
        });
        row.append(button);
      }
      if (Number(group.taskCount) > tasks.length) {
        row.append(element("span", "", `等 ${group.taskCount} 个`));
      }
      box.append(row);
    }
    return box;
  }

  // renderError shows a failure on the page instead of leaving it blank.
  //
  // A page that silently renders nothing looks exactly like a system with no
  // problems. That is the worst thing an alerting surface can do, so a fault is
  // stated where the problems would have been, with the message that caused it.
  function renderError(error) {
    const host = document.querySelector("#problems-list");
    if (!host) return;
    const message = String((error && error.message) || error || "未知错误");
    text("#problems-headline", "问题列表渲染失败");
    text("#problems-subline", "这不代表没有问题；是控制台没能把问题画出来。");
    host.replaceChildren(emptyState(
      "问题列表渲染失败",
      message + " —— 请把这条消息连同浏览器控制台的报错一起反馈。"));
  }

  // refresh polls the same endpoint the banner polls. One request feeds both, so
  // the page and the banner cannot show different numbers.
  async function refresh() {
    try {
      const response = await fetch("/v1/agent/alerts");
      if (!response.ok) return;
      render(await response.json());
    } catch (error) {
      // The page failing to load must not look like "no problems". Saying so is
      // the only way an unreachable agent differs from a healthy one.
      const host = document.querySelector("#problems-list");
      if (host && !host.children.length) {
        host.append(emptyState("读不到问题列表", "本地 agent 没有响应；这不代表没有问题。"));
      }
    }
  }

  window.tangyingProblems = {
    render, refresh, renderError,
    resetFilter: () => { activeFilter = "all"; },
  };
})();

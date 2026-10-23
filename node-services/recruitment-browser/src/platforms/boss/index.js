import { clickFirst, createRecruitmentActions, idFromUrl, queryString, readCard } from '../shared/recruitment-actions.js';

const RESULT_TIMEOUT = 15_000;
const SCROLL_SETTLE_MS = 1_500;
// 每个搜索最多翻页数（首页 + N 次滚动），限制单方向的操作量，降低风控风险
const MAX_SCROLL_PAGES = 3;
// 模拟真人浏览的停留时长范围（毫秒），实际取值随机
const MIN_STAY_MS = 8_000;
const MAX_STAY_MS = 15_000;
const randomStayMs = () => Math.floor(MIN_STAY_MS + Math.random() * (MAX_STAY_MS - MIN_STAY_MS));
const LOGIN_SELECTOR = [
  'a.header-login-btn',
  'a[href="/web/user/"]',
  'a[href*="/web/user/?intent=0"]',
].join(', ');

// ============ 风控/验证信号采集（只收集证据，是否暂停由上层 LLM 裁决）============
const RISK_KEYWORDS = ['安全验证', '滑块', '拖动', '请完成验证', '异常访问', '环境异常', '操作频繁'];
const RISK_SELECTORS = [
  '.geetest_panel', '.geetest_box', '[class*="geetest"]',
  '[class*="captcha"]', '[id*="captcha"]', '.verify-box',
];

// 采集页面上的风控/验证信号：命中后调用方快速失败并上报证据，
// 避免在验证页面上空等聊天框、持续给风控喂异常行为
const detectRiskSignals = async (page) => {
  const signals = [];
  for (const selector of RISK_SELECTORS) {
    const el = page.locator(selector).first();
    if (await el.isVisible().catch(() => false)) {
      const text = ((await el.textContent().catch(() => '')) || '').trim().slice(0, 80);
      signals.push(`selector:${selector}${text ? ` "${text}"` : ''}`);
    }
  }
  const bodyText = await page.evaluate(() => (document.body ? document.body.innerText.slice(0, 3000) : '')).catch(() => '');
  const hits = RISK_KEYWORDS.filter((keyword) => bodyText.includes(keyword));
  if (hits.length) signals.push(`keywords:${hits.join(',')}`);
  return signals;
};
const AUTHENTICATED_SELECTOR = [
  'a[ka="header-message"]',
  'a[ka="header-resume"]',
  'a[href*="/web/geek/chat"]',
  'li.nav-figure',
].join(', ');

export const decodeBossText = (value = '') => value.replace(/[\uE031-\uE03A]/g,
  (character) => String(character.codePointAt(0) - 0xE031));

// BOSS 详情页偶发 45s 导航超时（风控/加载慢），重试 3 次兜底
const gotoWithRetry = async (page, url, { attempts = 3 } = {}) => {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      return;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await page.waitForTimeout(2_000);
    }
  }
  throw lastError;
};

const BOSS_JOB_API_PATHS = [
  '/wapi/zpgeek/search/joblist.json',
  '/wapi/zpgeek/pc/recommend/job/list.json',
];

const list = (value) => Array.isArray(value)
  ? value.map((item) => typeof item === 'string' ? item : item?.name || item?.text)
    .filter(Boolean)
  : [];

export const mapBossApiJob = (item) => ({
  platformJobId: item.encryptJobId || '',
  title: item.jobName || '',
  company: item.brandName || '',
  city: [item.cityName, item.areaDistrict, item.businessDistrict].filter(Boolean).join('·'),
  salary: decodeBossText(item.salaryDesc || ''),
  description: [item.jobExperience, item.jobDegree, ...list(item.skills), ...list(item.jobLabels)]
    .filter(Boolean).join(' '),
  href: item.encryptJobId ? `https://www.zhipin.com/job_detail/${item.encryptJobId}.html` : '',
  facts: {
    experience: item.jobExperience || null,
    degree: item.jobDegree || null,
    companyIndustry: item.brandIndustry || null,
    companyStage: item.brandStageName || null,
    companyScale: item.brandScaleName || null,
    recruiterName: item.bossName || null,
    recruiterTitle: item.bossTitle || null,
    recruiterOnline: typeof item.bossOnline === 'boolean' ? item.bossOnline : null,
    recruiterActiveText: item.bossActiveTimeDesc || item.activeTimeDesc || null,
    recruiterId: item.encryptBossId || null,
    companyId: item.encryptBrandId || null,
    securityId: item.securityId || null,
    labels: list(item.jobLabels),
    skills: list(item.skills),
    benefits: list(item.welfareList),
  },
});

const ensureResponseMonitor = (page, state) => {
  if (state.bossJobMonitor) return state.bossJobMonitor;
  const monitor = { batches: [], cache: new Map(), pending: new Set(), responses: 0, hasMore: null, searchUrl: null };
  page.on('response', (response) => {
    if (!BOSS_JOB_API_PATHS.some((path) => response.url().includes(path))) return;
    const capture = (async () => {
      if (response.status() < 200 || response.status() >= 300) return;
      const payload = await response.json();
      const items = payload?.code === 0 ? payload?.zpData?.jobList : null;
      if (!Array.isArray(items)) return;
      const jobs = items.map(mapBossApiJob).filter((job) => job.platformJobId);
      if (jobs.length) {
        monitor.batches.push(jobs);
        jobs.forEach((job) => monitor.cache.set(job.platformJobId, job));
      }
      monitor.responses += 1;
      monitor.hasMore = payload.zpData?.hasMore ?? monitor.hasMore;
    })().catch(() => null);
    monitor.pending.add(capture);
    capture.finally(() => monitor.pending.delete(capture));
  });
  state.bossJobMonitor = monitor;
  return monitor;
};

const drainApiJobs = async (page, monitor, limit) => {
  await page.waitForTimeout(250);
  await Promise.all([...monitor.pending]);
  const batches = monitor.batches.splice(0);
  const captured = batches.length ? batches.flat() : [...monitor.cache.values()];
  const jobs = [...new Map(captured
    .filter((job) => job.platformJobId)
    .map((job) => [job.platformJobId, job])).values()];
  return jobs.slice(0, limit);
};

const jobCards = async (page) => {
  const wrappers = page.locator('li.job-card-wrapper');
  if (await wrappers.count() > 0) return wrappers;
  return page.locator('ul.rec-job-list li.job-card-box, .job-list-container li.job-card-box');
};

const BOSS_EDITOR_SELECTORS = [
  '#chat-input',
  '.chat-input',
  '.editor-content',
  '[contenteditable="true"]',
  'textarea.input-area',
  '.input-area',
  'textarea[placeholder*="打招呼"]',
  'textarea[placeholder*="输入"]',
  'textarea[placeholder*="内容"]',
  'textarea[placeholder*="聊"]',
  '[placeholder*="沟通"]',
];

// 在 page 的所有 frame（含 iframe）里找第一个可见的聊天编辑器
const findEditorInPage = async (targetPage) => {
  for (const frame of targetPage.frames()) {
    for (const selector of BOSS_EDITOR_SELECTORS) {
      const candidate = frame.locator(selector).first();
      if (await candidate.isVisible().catch(() => false)) return { editor: candidate, frame };
    }
  }
  return null;
};

// 在整个浏览器上下文（所有标签页 × 所有 frame）里找聊天编辑器
// BOSS 点击「立即沟通」后可能新开标签页，或把 IM 放在 iframe 里
const findChatEditor = async (page) => {
  const pages = page.context().pages();
  for (const targetPage of pages) {
    const found = await findEditorInPage(targetPage);
    if (found) return { ...found, page: targetPage };
  }
  return null;
};

// 在 page 的所有 frame 里点击第一个可见的匹配元素
const clickInFrames = async (targetPage, selectors) => {
  for (const frame of targetPage.frames()) {
    for (const selector of selectors) {
      const target = frame.locator(selector).first();
      if (await target.isVisible().catch(() => false)) {
        await target.click().catch(() => false);
        return true;
      }
    }
  }
  return false;
};

// 诊断信息：当前所有标签页 URL + 输入类元素的 placeholder/可见性，失败时写入 reason 便于排查
const editorDiagnostics = async (page) => {
  const ctx = page.context();
  const urls = ctx.pages().map((p) => p.url());
  const dumps = [];
  for (const p of ctx.pages()) {
    const dump = await p.evaluate(() => Array.from(document.querySelectorAll('textarea, [contenteditable="true"]'))
      .map((el) => `${el.tagName}#${el.id}.${String(el.className).slice(0, 30)} ph="${el.getAttribute('placeholder') || ''}" visible=${!!el.offsetParent}`)).catch(() => []);
    dumps.push(...dump.slice(0, 5));
  }
  return `urls=${JSON.stringify(urls)} inputs=${JSON.stringify(dumps.slice(0, 10))}`;
};

// 发送完成后关闭聊天对话框：新开的标签页直接关掉；同页内联面板找关闭按钮，找不到按 Esc
const closeChatDialog = async (page, chatPage) => {
  try {
    if (chatPage && chatPage !== page) {
      await chatPage.close().catch(() => {});
      return;
    }
    const closed = await clickInFrames(page, ['.chat-close-btn', '.im-close', '.close-chat', '[ka="im-close"]', '.chat-close', '.dialog-close', '.icon-close']);
    if (!closed) await page.keyboard.press('Escape').catch(() => {});
  } catch {
    // 关闭失败不影响主流程
  }
};

const definition = {
  card: 'li.job-card-wrapper, ul.rec-job-list li.job-card-box, .job-list-container li.job-card-box',
  fields: {
    id: ['[data-job-id]'],
    href: ['a.job-card-left', 'a[href*="/job_detail/"]'],
    title: ['.job-name', '.job-title'],
    company: ['.company-name', '.boss-name'],
    city: ['.job-area', '.job-area-wrapper'],
    salary: ['.job-salary', '.salary'],
    description: ['.job-card-footer', '.tag-list'],
  },
  jobId: idFromUrl,
  scrollContainers: ['.job-list-container', '.job-list-box'],
  buildSearchUrl: ({ keyword, cityCode }, filters) => queryString('https://www.zhipin.com/web/geek/jobs', {
    city: cityCode, query: keyword, jobType: filters.jobType, salary: filters.salary,
    experience: filters.experience, degree: filters.degree, scale: filters.scale,
    industry: filters.industry, stage: filters.stage,
  }),
  authenticated: async (page) => {
    if (page.url().includes('/web/user')) return false;
    const loginEntry = page.locator(LOGIN_SELECTOR).first();
    if (await loginEntry.isVisible().catch(() => false)) return false;
    return page.locator(AUTHENTICATED_SELECTOR).first().isVisible().catch(() => false);
  },
  searchJobs: async (page, input, { state }) => {
    if (!(await definition.authenticated(page))) throw new Error('AUTHENTICATION_REQUIRED');
    const monitor = ensureResponseMonitor(page, state);
    monitor.batches.length = 0;
    monitor.scrollCount = 0; // 新搜索重置翻页计数
    const search = input.search || { keyword: '', cityCode: '' };
    const searchUrl = definition.buildSearchUrl(search, input.filters || {});
    if (monitor.searchUrl !== searchUrl) monitor.cache.clear();
    monitor.searchUrl = searchUrl;
    if (page.url() !== searchUrl) {
      await page.goto(searchUrl, { waitUntil: 'commit', timeout: 45_000 });
    }
    await page.locator(AUTHENTICATED_SELECTOR).first()
      .waitFor({ state: 'visible', timeout: RESULT_TIMEOUT })
      .catch(() => null);
    if (!(await definition.authenticated(page))) throw new Error('AUTHENTICATION_REQUIRED');
    await page.locator('.job-list-container').first()
      .waitFor({ state: 'visible', timeout: RESULT_TIMEOUT })
      .catch(() => null);
    await page.locator(definition.card).first()
      .waitFor({ state: 'visible', timeout: RESULT_TIMEOUT })
      .catch(() => null);

    // 初始页面加载后停留一段时间（8-15 秒随机），并缓慢向下滚动，模拟真人浏览
    // 滚动次数/步长/中途停留全部随机，避免"每次 4 步、每步 600px"的固定机器节律
    const initialStayMs = randomStayMs();
    const scrollSteps = 3 + Math.floor(Math.random() * 4); // 3-6 次
    const stepDelayMs = Math.floor(initialStayMs / scrollSteps);
    const stepDistance = 400 + Math.floor(Math.random() * 400); // 400-800px
    for (let step = 0; step < scrollSteps; step += 1) {
      await page.evaluate((distance) => window.scrollBy({ top: distance, behavior: 'auto' }), stepDistance);
      await page.waitForTimeout(stepDelayMs);
      // 约 1/4 概率中途多停留一会，模拟被某条岗位吸引
      if (Math.random() < 0.25) await page.waitForTimeout(1500 + Math.random() * 3500);
    }

    return { searchUrl, currentUrl: page.url(), responseCapture: true, sideEffect: false };
  },
  collectVisibleJobs: async (page, input = {}, { state }) => {
    const monitor = ensureResponseMonitor(page, state);
    const apiJobs = await drainApiJobs(page, monitor, input.limit ?? 100);
    if (apiJobs.length) {
      return {
        jobs: apiJobs,
        discovered: apiJobs.length,
        source: 'network-response',
        responses: monitor.responses,
        hasMore: monitor.hasMore,
        sideEffect: false,
      };
    }
    const cards = await jobCards(page);
    const count = Math.min(await cards.count(), input.limit ?? 100);
    const jobs = [];
    for (let index = 0; index < count; index += 1) {
      const job = await readCard(cards.nth(index), definition, page.url());
      job.salary = decodeBossText(job.salary);
      if (!job.city && job.description) {
        job.city = job.description.startsWith(job.company)
          ? job.description.slice(job.company.length).trim()
          : job.description;
      }
      jobs.push(job);
    }
    const uniqueJobs = [...new Map(jobs
      .filter((job) => job.platformJobId || job.href)
      .map((job) => [job.platformJobId || job.href, job])).values()];
    return { jobs: uniqueJobs, discovered: uniqueJobs.length, source: 'dom-fallback', sideEffect: false };
  },
  scrollJobList: async (page, input = {}, { state }) => {
    const monitor = ensureResponseMonitor(page, state);
    const responseCountBefore = monitor.responses;
    const cards = await jobCards(page);
    const before = await cards.count();
    const cursorBefore = before > 0
      ? await cards.nth(before - 1).locator('a[href*="/job_detail/"]').first().getAttribute('href').catch(() => null)
      : null;

    // 翻页上限：超过 MAX_SCROLL_PAGES 后假装没有更多数据，结束本方向的翻页
    monitor.scrollCount = (monitor.scrollCount ?? 0) + 1;
    if (monitor.scrollCount > MAX_SCROLL_PAGES) {
      return {
        before,
        after: before,
        loaded: 0,
        cursorBefore,
        cursorAfter: cursorBefore,
        changed: false,
        apiResponses: 0,
        hasMore: false,
        sideEffect: false,
      };
    }

    // 每个页面停留一段时间（8-15 秒随机），期间分多次向下滚动，模拟真人浏览
    // 滚动次数/步长/中途停留全部随机，避免固定节律被识别为脚本
    const minPageStayMs = randomStayMs();
    const scrollSteps = 3 + Math.floor(Math.random() * 4); // 3-6 次
    const stepDelayMs = Math.floor(minPageStayMs / scrollSteps);
    const stepDistance = Math.min(Math.max(Number(input.distance ?? 640), 240), 1_000)
      * (0.75 + Math.random() * 0.5); // 在调用方给定距离基础上再浮动 ±25%

    for (let step = 0; step < scrollSteps; step += 1) {
      if (before > 0) await cards.nth(Math.min(before - 1, step * 2)).scrollIntoViewIfNeeded().catch(() => null);
      await page.evaluate((distance) => window.scrollBy({ top: distance, behavior: 'auto' }), stepDistance);
      await page.waitForTimeout(stepDelayMs);
      // 约 1/4 概率中途多停留一会，模拟被某条岗位吸引
      if (Math.random() < 0.25) await page.waitForTimeout(1500 + Math.random() * 3500);
    }

    const after = await cards.count();
    const cursorAfter = after > 0
      ? await cards.nth(after - 1).locator('a[href*="/job_detail/"]').first().getAttribute('href').catch(() => null)
      : null;
    return {
      before,
      after,
      loaded: Math.max(after - before, 0),
      cursorBefore,
      cursorAfter,
      changed: after > before || cursorAfter !== cursorBefore,
      apiResponses: monitor.responses - responseCountBefore,
      hasMore: monitor.hasMore,
      sideEffect: false,
    };
  },
  inspectVisibleJobs: async (page) => {
    const rootSelectors = [
      'li.job-card-wrapper',
      'li.job-card-box',
      '.job-list-container li',
    ];
    const fieldSelectors = [
      '.job-name', '.company-name', '.job-area', '.job-area-wrapper',
      '.salary', '.job-salary', '.job-card-footer', '.job-card-body',
    ];
    const roots = [];
    for (const selector of rootSelectors) {
      const cards = page.locator(selector);
      roots.push({ selector, count: await cards.count() });
    }
    const sample = await jobCards(page);
    const fields = [];
    for (const selector of fieldSelectors) {
      const targets = sample.first().locator(selector);
      const count = await targets.count();
      fields.push({
        selector,
        count,
        text: count > 0 ? await targets.first().textContent().catch(() => null) : null,
      });
    }
    return { available: true, roots, fields, sideEffect: false };
  },
  prepareContact: async (page, job) => {
    await gotoWithRetry(page, job.href);
    const contactActionVisible = await page.locator('a.btn.btn-startchat').first().isVisible().catch(() => false);
    const editorVisible = await page.locator('#chat-input').first().isVisible().catch(() => false);
    return {
      platformJobId: job.platformJobId,
      status: contactActionVisible || editorVisible ? 'READY' : 'UNAVAILABLE',
      currentUrl: page.url(),
      contactActionVisible,
      editorVisible,
      reason: contactActionVisible || editorVisible ? null : 'CONTACT_ACTION_UNAVAILABLE',
    };
  },
  sendContact: async (page, job, input) => {
    await gotoWithRetry(page, job.href);

    // 风控信号早检：命中立即快速失败并上报证据（是否暂停由上层 LLM 裁决），
    // 避免在验证页面上空等聊天框 20 秒、逐个岗位重复撞击验证
    const earlySignals = await detectRiskSignals(page);
    if (earlySignals.length) {
      return { platformJobId: job.platformJobId, status: 'FAILED', reason: `RISK_SIGNAL ${earlySignals.join(' | ')}` };
    }

    // 已沟通过的岗位详情页显示「继续沟通」而非「立即沟通」：
    // 视为已投递，直接跳过（不再发第二条招呼语，也不计入失败次数）
    const alreadyContacted = await clickFirst(page, ['a:has-text("继续沟通")', 'button:has-text("继续沟通")', '.btn-continue']);
    if (alreadyContacted) {
      return { platformJobId: job.platformJobId, status: 'SKIPPED', reason: 'ALREADY_CONTACTED' };
    }

    // 模拟阅读 JD：停留 2-6 秒并小幅随机滚动后再点击「立即沟通」，贴合真人节奏
    await page.waitForTimeout(2000 + Math.floor(Math.random() * 4000));
    await page.evaluate(() => window.scrollBy({ top: 200 + Math.floor(Math.random() * 400), behavior: 'smooth' }))
      .catch(() => null);
    await page.waitForTimeout(500 + Math.floor(Math.random() * 1500));

    const clicked = await clickFirst(page, ['a.btn.btn-startchat', '.btn-startchat', '[ka="job-detail-chat-btn"]']);
    if (!clicked) return { platformJobId: job.platformJobId, status: 'SKIPPED', reason: 'CONTACT_ACTION_UNAVAILABLE' };

    if (input.greeting) {
      // BOSS 点击「立即沟通」后可能新开标签页或弹出浮层，最多等 20 秒，
      // 并跨标签页/iframe 搜索聊天编辑器
      let found = null;
      for (let attempt = 0; attempt < 40 && !found; attempt += 1) {
        found = await findChatEditor(page);
        if (!found) {
          // 第 5 秒还没出聊天框，可能是第一次点击没生效（被弹层拦截），补点一次
          if (attempt === 10) {
            await clickFirst(page, ['a.btn.btn-startchat', '.btn-startchat', '[ka="job-detail-chat-btn"]', 'button:has-text("继续沟通")', 'a:has-text("继续沟通")']);
          }
          await page.waitForTimeout(500);
        }
      }

      if (!found) {
        const signals = await detectRiskSignals(page);
        return {
          platformJobId: job.platformJobId,
          status: 'FAILED',
          reason: `CHAT_EDITOR_NOT_FOUND${signals.length ? ` RISK_SIGNAL ${signals.join(' | ')}` : ''} ${await editorDiagnostics(page)}`,
        };
      }

      const { editor, page: chatPage } = found;
      await editor.fill(input.greeting);
      await chatPage.waitForTimeout(300);

      // 优先点发送按钮；找不到则回退为在编辑器里按 Enter 发送
      const sendClicked = await clickInFrames(chatPage, ['button[type="send"]', '.btn-send', '.send-message', 'button:has-text("发送")', '[ka="im-send"]', '.chat-send']);
      if (!sendClicked) {
        await editor.press('Enter');
      }

      await chatPage.waitForTimeout(800);

      // 验证方式 1：编辑器被清空说明已发送
      const editorCleared = await editor.inputValue().catch(() => '') === ''
        && await editor.textContent().catch(() => '') === '';

      // 验证方式 2：聊天区域出现包含招呼语的消息
      let messageVisible = false;
      for (let attempt = 0; attempt < 10; attempt += 1) {
        const ownMessage = chatPage.locator('.message-item.self, .message-list .self, .chat-message-list .self, .im-message-list .self, .message-content')
          .filter({ hasText: input.greeting.slice(0, 16) })
          .first();
        if (await ownMessage.isVisible().catch(() => false)) {
          messageVisible = true;
          break;
        }
        await chatPage.waitForTimeout(400);
      }

      if (!editorCleared && !messageVisible) {
        await closeChatDialog(page, chatPage);
        return { platformJobId: job.platformJobId, status: 'FAILED', reason: 'MESSAGE_NOT_VERIFIED' };
      }
      // 发送成功，关闭聊天对话框后再进行下一个岗位的点击
      await closeChatDialog(page, chatPage);
    }
    return { platformJobId: job.platformJobId, status: 'SUCCEEDED', reason: null };
  },
};

export default { id: 'boss', hosts: ['zhipin.com'], capabilities: ['session', 'discover', 'contact'], actions: createRecruitmentActions(definition) };

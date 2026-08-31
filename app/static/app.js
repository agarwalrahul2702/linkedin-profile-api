const $ = (selector) => document.querySelector(selector);

const loginForm = $("#login-form");
const profileForm = $("#profile-form");
const sessionSummary = $("#session-summary");
const emptyState = $("#empty-state");
const loadingState = $("#loading-state");
const profileResult = $("#profile-result");
const copyButton = $("#copy-json");
const toast = $("#toast");
const deploymentNote = $("#deployment-note");
const localDashboard = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
let currentProfile = null;
let toastTimer = null;

const sampleProfile = {
  public_identifier: "sample-profile",
  profile_url: "https://www.linkedin.com/in/sample-profile/",
  name: "Aarav Mehta",
  headline: "Product Engineer · Building dependable data products",
  location: "Bengaluru, Karnataka, India",
  about: "Engineer focused on turning complex systems into simple, useful products. Experienced across APIs, data platforms, and developer tooling.",
  profile_image_url: null,
  background_image_url: null,
  experience: [
    { title: "Senior Product Engineer", company: "Northstar Labs", location: "Bengaluru", date_range: "03/2023 - Present", description: "Leading API platform development and reliability initiatives." },
    { title: "Software Engineer", company: "Vertex Systems", location: "Remote", date_range: "07/2020 - 02/2023", description: "Built data pipelines and internal developer tools." },
  ],
  education: [{ school: "Institute of Technology", degree: "B.Tech", field_of_study: "Computer Science", date_range: "2016 - 2020" }],
  skills: ["Python", "FastAPI", "Distributed Systems", "PostgreSQL", "Product Engineering"],
  certifications: [{ name: "Cloud Architecture Professional", authority: "Cloud Academy", date_range: "2024" }],
  languages: [{ name: "English", proficiency: "Professional working" }, { name: "Hindi", proficiency: "Native" }],
};

function notify(message, isError = false) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  toastTimer = setTimeout(() => { toast.className = "toast"; }, 4200);
}

async function readResponse(response) {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function setButtonLoading(button, loading, label) {
  button.disabled = loading;
  button.querySelector("span").textContent = loading ? "Please wait…" : label;
}

function setConnected(connected, detail = "") {
  sessionSummary.hidden = !connected;
  loginForm.hidden = connected || !localDashboard;
  deploymentNote.hidden = connected || localDashboard;
  if (connected) $("#session-detail").textContent = detail || "LinkedIn session is active.";
}

async function checkSession() {
  try {
    const response = await fetch("/health/linkedin-session");
    const data = await readResponse(response);
    setConnected(Boolean(data.valid), data.detail);
  } catch (_) {
    setConnected(false);
  }
}

$("#toggle-password").addEventListener("click", () => {
  const input = $("#linkedin-password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  $("#toggle-password").textContent = show ? "Hide" : "Show";
});

$("#change-account").addEventListener("click", () => {
  if (!localDashboard) return;
  setConnected(false);
  $("#linkedin-id").focus();
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#login-button");
  const passwordInput = $("#linkedin-password");
  setButtonLoading(button, true, "Connect session");
  try {
    const response = await fetch("/api/v1/session/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        linkedin_id: $("#linkedin-id").value.trim(),
        password: passwordInput.value,
        user_agent: navigator.userAgent,
      }),
    });
    const data = await readResponse(response);
    setConnected(true, data.detail);
    passwordInput.value = "";
    notify("LinkedIn session connected successfully.");
    $("#profile-url").focus();
  } catch (error) {
    passwordInput.value = "";
    notify(error.message, true);
  } finally {
    setButtonLoading(button, false, "Connect session");
  }
});

function create(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function initials(name) {
  return (name || "LI").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function addSection(container, title, content) {
  if (!content) return;
  const section = create("section", "data-section");
  section.append(create("h3", "", title), content);
  container.append(section);
}

function renderTimeline(items) {
  if (!items?.length) return null;
  const timeline = create("div", "timeline");
  items.forEach((item) => {
    const row = create("article", "timeline-item");
    row.append(create("span", "timeline-dot"));
    row.append(create("h4", "", item.title || "Role"));
    if (item.company) row.append(create("p", "timeline-company", item.company));
    const meta = [item.date_range, item.location].filter(Boolean).join(" · ");
    if (meta) row.append(create("p", "item-meta", meta));
    if (item.description) row.append(create("p", "item-description", item.description));
    timeline.append(row);
  });
  return timeline;
}

function renderEducation(items) {
  if (!items?.length) return null;
  const grid = create("div", "education-grid");
  items.forEach((item) => {
    const card = create("article", "education-card");
    card.append(create("h4", "", item.school || "Education"));
    const study = [item.degree, item.field_of_study].filter(Boolean).join(", ");
    if (study) card.append(create("p", "", study));
    if (item.date_range) card.append(create("p", "item-meta", item.date_range));
    grid.append(card);
  });
  return grid;
}

function renderChips(items) {
  if (!items?.length) return null;
  const list = create("div", "chip-list");
  items.forEach((item) => list.append(create("span", "chip", item)));
  return list;
}

function renderCompact(items, primaryKey, secondaryKeys) {
  if (!items?.length) return null;
  const list = create("div", "compact-list");
  items.forEach((item) => {
    const card = create("div", "compact-item");
    card.append(create("strong", "", item[primaryKey] || "Not specified"));
    const secondary = secondaryKeys.map((key) => item[key]).filter(Boolean).join(" · ");
    if (secondary) card.append(create("span", "", secondary));
    list.append(card);
  });
  return list;
}

function renderProfile(profile) {
  currentProfile = profile;
  profileResult.replaceChildren();

  const hero = create("div", "profile-hero");
  const cover = create("div", "profile-cover");
  if (profile.background_image_url) cover.style.backgroundImage = `url("${profile.background_image_url.replaceAll('"', "%22")}")`;
  const identity = create("div", "profile-identity");
  let avatar;
  if (profile.profile_image_url) {
    avatar = create("img", "profile-avatar");
    avatar.src = profile.profile_image_url;
    avatar.alt = `${profile.name || "LinkedIn profile"} photo`;
    avatar.referrerPolicy = "no-referrer";
    avatar.addEventListener("error", () => {
      const fallback = create("div", "profile-avatar", initials(profile.name));
      avatar.replaceWith(fallback);
    }, { once: true });
  } else {
    avatar = create("div", "profile-avatar", initials(profile.name));
  }
  identity.append(avatar, create("h2", "", profile.name || "LinkedIn profile"));
  if (profile.headline) identity.append(create("p", "profile-headline", profile.headline));
  if (profile.location) identity.append(create("p", "profile-location", profile.location));
  hero.append(cover, identity);

  const stats = create("div", "profile-stats");
  [
    [profile.experience?.length || 0, "Roles"],
    [profile.education?.length || 0, "Education"],
    [profile.skills?.length || 0, "Skills"],
    [profile.certifications?.length || 0, "Certs"],
    [profile.languages?.length || 0, "Languages"],
  ].forEach(([value, label]) => {
    const stat = create("div", "stat");
    stat.append(create("strong", "", String(value)), create("span", "", label));
    stats.append(stat);
  });

  const sections = create("div", "profile-sections");
  if (profile.about) addSection(sections, "About", create("p", "about-copy", profile.about));
  addSection(sections, "Experience", renderTimeline(profile.experience));
  addSection(sections, "Education", renderEducation(profile.education));
  addSection(sections, "Skills", renderChips(profile.skills));
  addSection(sections, "Certifications", renderCompact(profile.certifications, "name", ["authority", "date_range"]));
  addSection(sections, "Languages", renderCompact(profile.languages, "name", ["proficiency"]));

  profileResult.append(hero, stats, sections);
  emptyState.hidden = true;
  loadingState.hidden = true;
  profileResult.hidden = false;
  copyButton.hidden = false;
}

profileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#fetch-button");
  emptyState.hidden = true;
  profileResult.hidden = true;
  copyButton.hidden = true;
  loadingState.hidden = false;
  setButtonLoading(button, true, "Extract profile");
  try {
    const response = await fetch("/api/v1/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ linkedin_url: $("#profile-url").value.trim() }),
    });
    renderProfile(await readResponse(response));
    notify("Profile extracted successfully.");
  } catch (error) {
    loadingState.hidden = true;
    emptyState.hidden = false;
    notify(error.message, true);
    if (/session|LI_AT|401/i.test(error.message)) setConnected(false);
  } finally {
    setButtonLoading(button, false, "Extract profile");
  }
});

copyButton.addEventListener("click", async () => {
  if (!currentProfile) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(currentProfile, null, 2));
    notify("Structured JSON copied to clipboard.");
  } catch (_) {
    notify("Clipboard access was unavailable.", true);
  }
});

$("#preview-sample").addEventListener("click", () => {
  renderProfile(sampleProfile);
  notify("Sample profile loaded. Submit a URL for live data.");
});

checkSession();

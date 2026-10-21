// SPDX-License-Identifier: GPL-3.0-only
// Cooperative public tasks require a separate review action. Never auto-publish page context.

import { VolparossaCooperativeCompute } from "./VolparossaCooperativeCompute.sys.mjs";

const encoder = new TextEncoder();
const LICENSES = ["GPL-3.0-only", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"];
const MESSAGES = Object.freeze({
  busy: "The cooperative service is busy or quarantined. Nothing was sent to a cloud provider.",
  not_configured: "Configure browser.volparossa.compute.public_socket with your core's public-task service socket.",
  public_consent_required: "Review the exact question and text, confirm your sharing rights and consent before sending.",
  invalid_license: "Select the license that you are authorized to apply to this public input.",
  invalid_question: "Enter a question of at most 512 UTF-8 bytes.",
  invalid_context: "Enter public text of at most 4096 UTF-8 bytes. Oversized text is never silently truncated.",
  cancelled: "Cancelled; remote worker termination was confirmed. Already shared public content and receipts may remain.",
  cleanup_unconfirmed: "Stopped waiting. Remote worker termination is not confirmed; no completed answer is claimed.",
  execution_failed: "The cooperative task could not complete. No local-only answer or cloud fallback was substituted.",
  deadline_exceeded: "The task reached its time budget and was stopped. Already shared public content and receipts may remain.",
  storage_bound: "The cooperative service reached its retained-task storage budget. No cloud fallback was used.",
  invalid_response: "The cooperative core returned an incompatible or invalid response.",
  unavailable: "The cooperative core is unavailable. No cloud fallback was used.",
});

export function createVolparossaCooperativePanel(document, container) {
  const create = (tag, value) => {
    const element = document.createElement(tag);
    if (value !== undefined) {
      element.textContent = value;
    }
    return element;
  };
  const element = create("section");
  element.id = "volparossa-cooperative-compute";
  element.dataset.state = "review";
  element.append(create("h2", "VOLPAROSSA AI"));
  element.append(create("p", "Cooperative network · Public tasks · Real peer execution and combined answers"));
  const warning = create("p", "This mode shares the question, selected text and derived work with other participants. Use Private local for confidential content. Shared public content and receipts cannot be guaranteed erased.");
  warning.className = "volparossa-public-warning";
  element.append(warning);

  const question = create("textarea");
  question.id = "volparossa-public-question";
  question.rows = 2;
  const questionLabel = create("label", "Public question (512 UTF-8 bytes maximum)");
  questionLabel.htmlFor = question.id;
  const context = create("textarea");
  context.id = "volparossa-public-context";
  context.rows = 7;
  const contextLabel = create("label", "Exact text to share (4096 UTF-8 bytes maximum)");
  contextLabel.htmlFor = context.id;
  element.append(questionLabel, question, contextLabel, context);
  const license = create("select");
  license.id = "volparossa-public-license";
  const empty = create("option", "Choose the applicable public license…");
  empty.value = "";
  license.append(empty);
  for (const name of LICENSES) {
    const option = create("option", name);
    option.value = name;
    license.append(option);
  }
  const licenseLabel = create("label", "Content license (not assigned automatically)");
  licenseLabel.htmlFor = license.id;
  element.append(licenseLabel, license);
  const checkbox = (id, label) => {
    const input = create("input");
    input.id = id;
    input.type = "checkbox";
    input.checked = false;
    const description = create("label", label);
    description.htmlFor = id;
    const row = create("div");
    row.className = "volparossa-consent-row";
    row.append(input, description);
    element.append(row);
    return input;
  };
  const rights = checkbox("volparossa-public-rights", "I have the rights to share this question and text under the selected license, including any required attribution.");
  const consent = checkbox("volparossa-public-consent", "I reviewed the exact text above and authorize its public disclosure to cooperative peers. It contains no confidential or private information.");
  const actions = create("div");
  actions.className = "volparossa-compute-actions";
  const submit = create("button", "Send public task to the network");
  submit.id = "volparossa-public-submit";
  const cancel = create("button", "Cancel task");
  cancel.id = "volparossa-public-cancel";
  submit.type = cancel.type = "button";
  submit.disabled = true;
  cancel.disabled = true;
  actions.append(submit, cancel);
  const status = create("p", "Review the text and sharing permissions. Nothing has been sent.");
  status.id = "volparossa-public-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const output = create("pre");
  output.id = "volparossa-public-answer";
  output.setAttribute("aria-label", "Cooperative network answer");
  element.append(actions, status, output);
  container.append(element);

  let client = null;
  let task = null;
  let running = false;
  let destroyed = false;
  const fields = [question, context, license, rights, consent];
  const validText = (field, limit) => field.value.trim() && !field.value.includes("\0") &&
    encoder.encode(field.value).length <= limit;
  const ready = () => !destroyed && !running && rights.checked && consent.checked &&
    LICENSES.includes(license.value) && validText(question, 512) && validText(context, 4096);
  const update = () => { submit.disabled = !ready(); };
  const resetConsent = () => {
    rights.checked = consent.checked = false;
    update();
  };
  for (const field of [question, context, license]) {
    field.addEventListener("input", resetConsent);
    field.addEventListener("change", resetConsent);
  }
  rights.addEventListener("change", update);
  consent.addEventListener("change", update);
  const report = error => {
    element.dataset.state = error?.code === "cancelled" ? "cancelled" : "error";
    status.textContent = MESSAGES[error?.code] ?? MESSAGES.unavailable;
  };
  const run = async () => {
    if (!ready()) {
      if (!running && !destroyed) {
        report({ code: "public_consent_required" });
      }
      return;
    }
    // Freeze the exact reviewed bytes before any asynchronous connection or capability exchange.
    const input = { question: question.value, context: context.value, license: license.value,
      public_content: true, rights_confirmed: true };
    running = true;
    fields.forEach(field => { field.disabled = true; });
    submit.disabled = true;
    cancel.disabled = true;
    output.textContent = "";
    element.dataset.state = "connecting";
    status.textContent = "Checking the cooperative core's public-task capabilities…";
    try {
      client = await VolparossaCooperativeCompute.connect();
      if (destroyed) {
        client.close();
        return;
      }
      if (client.capabilities.quarantined) {
        throw { code: "busy" };
      }
      task = client.submit({ ...input, onAdmitted: () => {
        element.dataset.state = "running";
        status.textContent = "Public task admitted. Waiting for peer work, synthesis and confirmed worker termination…";
      } });
      cancel.disabled = false;
      const result = await task.finished;
      if (!destroyed) {
        output.textContent = result.output.text;
        element.dataset.state = result.answer_complete ? "complete" : "incomplete";
        element.dataset.peerCount = String(result.provider_keys.length);
        element.dataset.synthesisLevels = String(result.synthesis_levels);
        element.dataset.cleanupConfirmed = "true";
        const mode = result.joining === "hierarchical_peer_synthesis" ? "Combined network answer" : "Network result";
        status.textContent = `${result.answer_complete ? mode : "Incomplete network answer"} · ${result.provider_keys.length} contributing peer(s) · ${result.synthesis_levels} synthesis level(s). Worker termination confirmed; public receipts retained. Model answers may still be incorrect.`;
      }
    } catch (error) {
      if (!destroyed) {
        report(error);
      }
    } finally {
      client?.close();
      client = null;
      task = null;
      running = false;
      if (!destroyed) {
        fields.forEach(field => { field.disabled = false; });
        resetConsent();
        cancel.disabled = true;
      }
    }
  };
  submit.addEventListener("click", run);
  cancel.addEventListener("click", async () => {
    if (!task || destroyed) {
      return;
    }
    cancel.disabled = true;
    element.dataset.state = "cancelling";
    status.textContent = "Cancellation requested; waiting for remote worker termination. Public content already shared may remain.";
    try { await task.cancel(); } catch (error) { if (!destroyed) { report(error); } }
  });
  return {
    element,
    // Native Ask only prefills. It never connects, signs, publishes, starts a job or grants consent.
    async ask(prompt, selectedContext) {
      if (running || destroyed) {
        return;
      }
      question.value = typeof prompt === "string" ? prompt : "";
      context.value = typeof selectedContext === "string" ? selectedContext : "";
      license.value = "";
      output.textContent = "";
      resetConsent();
      element.dataset.state = "review";
      delete element.dataset.peerCount;
      delete element.dataset.synthesisLevels;
      delete element.dataset.cleanupConfirmed;
      status.textContent = "Review the exact text and sharing permissions. Nothing has been sent.";
    },
    destroy() {
      destroyed = true;
      client?.close();
      question.value = context.value = license.value = output.textContent = "";
      rights.checked = consent.checked = false;
      element.remove();
    },
  };
}

// SPDX-License-Identifier: GPL-3.0-only
// Native sidebar UI: untrusted page text and answers are never HTML or privileged commands.

import { VolparossaCompute } from "./VolparossaCompute.sys.mjs";

const MESSAGES = Object.freeze({
  busy: "The local worker is busy. No cloud fallback was used.",
  not_configured: "Configure browser.volparossa.compute.socket with the owner's local broker socket.",
  invalid_question: "Enter a question of at most 512 UTF-8 bytes.",
  invalid_context: "Choose explicit context of at most 4096 UTF-8 bytes. Edit the excerpt before retrying.",
  cancelled: "Cancelled; the local worker confirmed cleanup.",
  cleanup_unconfirmed: "Stopped waiting. Worker cleanup is not confirmed; no successful answer is claimed.",
  execution_failed: "Local execution failed. No cloud fallback was used.",
  invalid_response: "The local broker returned an incompatible or invalid response.",
  unavailable: "The configured local broker is unavailable. No cloud fallback was used.",
});

export function createVolparossaComputePanel(document, container) {
  const create = (tag, value) => {
    const element = document.createElement(tag);
    if (value !== undefined) {
      element.textContent = value;
    }
    return element;
  };
  const element = create("section");
  element.id = "volparossa-private-compute";
  element.append(create("h2", "Project VOLPAROSSA"));
  element.append(create("p", "Private local compute · Explicit context only · No public cache, training or cloud fallback"));
  const question = create("textarea");
  question.id = "volparossa-question";
  question.rows = 2;
  const questionLabel = create("label", "Question (512 UTF-8 bytes maximum)");
  questionLabel.htmlFor = question.id;
  const context = create("textarea");
  context.id = "volparossa-context";
  context.rows = 6;
  const contextLabel = create("label", "Selected context (4096 UTF-8 bytes maximum)");
  contextLabel.htmlFor = context.id;
  element.append(questionLabel, question, contextLabel, context);
  const actions = create("div");
  const submit = create("button", "Ask locally");
  const cancel = create("button", "Cancel");
  submit.type = cancel.type = "button";
  cancel.disabled = true;
  actions.append(submit, cancel);
  const status = create("p", "Ready. Text is sent only when you ask.");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const output = create("pre");
  output.setAttribute("aria-label", "Local model output");
  element.append(actions, status, output);
  container.append(element);
  let client = null;
  let task = null;
  let running = false;
  let destroyed = false;
  const report = error => {
    status.textContent = MESSAGES[error?.code] ?? MESSAGES.unavailable;
  };
  const run = async () => {
    if (running || destroyed) {
      return;
    }
    running = true;
    submit.disabled = true;
    cancel.disabled = true;
    output.textContent = "";
    status.textContent = "Checking the private local broker…";
    try {
      client = await VolparossaCompute.connect();
      if (destroyed) {
        client.close();
        return;
      }
      task = client.submit({
        question: question.value,
        context: context.value,
        onAdmitted: () => { status.textContent = "Running locally; waiting for worker cleanup before presenting the answer…"; },
      });
      cancel.disabled = false;
      const result = await task.finished;
      if (!destroyed) {
        output.textContent = result.output.text;
        status.textContent = result.answer_complete
          ? "Local answer received; cleanup confirmed. Model answers may still be incorrect."
          : "Incomplete local answer; cleanup confirmed. The task did not produce a complete answer.";
        status.textContent += ` Status: ${result.answer_status}.`;
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
      submit.disabled = false;
      cancel.disabled = true;
    }
  };
  submit.addEventListener("click", run);
  cancel.addEventListener("click", async () => {
    cancel.disabled = true;
    status.textContent = "Cancellation requested; waiting for cleanup confirmation…";
    try { await task?.cancel(); } catch (error) { report(error); }
  });
  return {
    element,
    async ask(prompt, selectedContext) {
      if (running || destroyed) {
        return;
      }
      question.value = typeof prompt === "string" ? prompt : "";
      context.value = typeof selectedContext === "string" ? selectedContext : "";
      await run();
    },
    destroy() {
      destroyed = true;
      client?.close();
      question.value = context.value = output.textContent = "";
      element.remove();
    },
  };
}

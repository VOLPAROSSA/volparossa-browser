// SPDX-License-Identifier: GPL-3.0-only
// Privileged cooperative PUBLIC-task IPC. Never import into web-content globals.

import { setTimeout, clearTimeout } from "resource://gre/modules/Timer.sys.mjs";

export const VOLPAROSSA_PUBLIC_PROVIDER = "volparossa:public";
export const PUBLIC_SOCKET_PREF = "browser.volparossa.compute.public_socket";
const VERSION = 1;
const MAX_REQUEST = 32768;
const MAX_RESPONSE = 65536;
const MAX_IDS = 256;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const errors = new Set([
  "busy", "invalid_request", "handshake_required", "no_such_task", "cancelled",
  "execution_failed", "cleanup_unconfirmed", "deadline_exceeded", "storage_bound",
]);

export class VolparossaCooperativeComputeError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function fail(code) {
  throw new VolparossaCooperativeComputeError(code);
}

function text(value, maximum, code) {
  if (typeof value !== "string" || !value.trim() || value.includes("\0") || encoder.encode(value).length > maximum) {
    fail(code);
  }
  return value;
}

/** One connection owns its pending work. Closing it never migrates work to another broker. */
export class VolparossaCooperativeCompute {
  static async connect({ socketPath = Services.prefs.getStringPref(PUBLIC_SOCKET_PREF, "") } = {}) {
    if (Services.appinfo.processType !== Ci.nsIXULRuntime.PROCESS_TYPE_DEFAULT) {
      fail("unavailable");
    }
    // Only explicit local-owner configuration, never a page URL or page-supplied endpoint.
    if (typeof socketPath !== "string" || !socketPath.startsWith("/") ||
        socketPath.includes("\0") || encoder.encode(socketPath).length > 107) {
      fail("not_configured");
    }
    const file = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    file.initWithPath(socketPath);
    const socketService = Cc["@mozilla.org/network/socket-transport-service;1"]
      .getService(Ci.nsISocketTransportService);
    let client;
    try {
      client = new VolparossaCooperativeCompute(socketService.createUnixDomainTransport(file));
      const request = client._request({ type: "capabilities" }, 5000);
      client.capabilities = await request.finished;
      return client;
    } catch (error) {
      client?.close();
      throw error instanceof VolparossaCooperativeComputeError ? error : new VolparossaCooperativeComputeError("unavailable");
    }
  }

  constructor(transport) {
    this.transport = transport;
    this.pending = new Map();
    this.issued = new Set();
    this.outgoing = [];
    this.closed = false;
    this.capabilities = null;
    this.active = null;
    this.header = new Uint8Array(4);
    this.headerUsed = 0;
    this.body = null;
    this.bodyUsed = 0;
    transport.setTimeout(Ci.nsISocketTransport.TIMEOUT_CONNECT, 5);
    this.input = transport.openInputStream(0, 0, 0);
    this.output = transport.openOutputStream(0, 0, 0).QueryInterface(Ci.nsIAsyncOutputStream);
    this.pump = Cc["@mozilla.org/network/input-stream-pump;1"].createInstance(Ci.nsIInputStreamPump);
    this.pump.init(this.input, 0, 0, true);
    this.pump.asyncRead(this);
  }

  QueryInterface = ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver", "nsIOutputStreamCallback"]);

  submit({ question, context, license, public_content, rights_confirmed, onAdmitted = () => {} }) {
    if (!this.capabilities || this.closed) {
      fail("unavailable");
    }
    if (this.active) {
      fail("busy");
    }
    text(question, 512, "invalid_question");
    text(context, 4096, "invalid_context");
    if (public_content !== true || rights_confirmed !== true) {
      fail("public_consent_required");
    }
    if (!["GPL-3.0-only", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"].includes(license)) {
      fail("invalid_license");
    }
    const request = this._request(
      { type: "submit", question, context, license, public_content, rights_confirmed },
      (this.capabilities.max_task_seconds + 15) * 1000,
      onAdmitted
    );
    this.active = request.id;
    return { ...request, cancel: () => this.cancel(request.id) };
  }

  cancel(taskId) {
    if (taskId !== this.active || !this.pending.has(taskId)) {
      return Promise.reject(new VolparossaCooperativeComputeError("no_such_task"));
    }
    if (this.pending.get(taskId).cancellation) {
      return this.pending.get(taskId).cancellation;
    }
    const request = this._request({ type: "cancel", task_id: taskId }, 5000);
    this.pending.get(taskId).cancellation = request.finished;
    return request.finished;
  }

  close(code = this.active ? "cleanup_unconfirmed" : "unavailable") {
    if (this.closed) {
      return;
    }
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new VolparossaCooperativeComputeError(code));
    }
    this.pending.clear();
    this.outgoing.length = 0;
    this.active = null;
    this.body = null;
    try { this.pump.cancel(Cr.NS_BINDING_ABORTED); } catch {}
    try { this.input.close(); } catch {}
    try { this.output.close(); } catch {}
    try { this.transport.close(Cr.NS_BINDING_ABORTED); } catch {}
  }

  _request(operation, timeout, onAdmitted = () => {}) {
    if (this.closed || this.issued.size >= MAX_IDS || this.outgoing.length >= 3) {
      fail("unavailable");
    }
    const id = Cc["@mozilla.org/uuid-generator;1"].getService(Ci.nsIUUIDGenerator)
      .generateUUID().toString().replace(/[{}-]/g, "");
    if (!/^[0-9a-f]{32}$/.test(id) || this.issued.has(id)) {
      fail("invalid_response");
    }
    const payload = encoder.encode(JSON.stringify({ version: VERSION, id, operation }));
    if (payload.length > MAX_REQUEST) {
      fail("invalid_request");
    }
    const frame = new Uint8Array(4 + payload.length);
    new DataView(frame.buffer).setUint32(0, payload.length);
    frame.set(payload, 4);
    const finished = new Promise((resolve, reject) => {
      this.pending.set(id, {
        type: operation.type, taskId: operation.task_id, resolve, reject, onAdmitted,
        admitted: false,
        timer: setTimeout(() => this.close(operation.type === "submit" ? "cleanup_unconfirmed" : "unavailable"), timeout),
      });
    });
    this.issued.add(id);
    this.outgoing.push({ frame, offset: 0 });
    try { this._flush(); } catch { this.close(); }
    return { id, finished };
  }

  _flush() {
    while (this.outgoing.length && !this.closed) {
      const next = this.outgoing[0];
      const bytes = next.frame.subarray(next.offset, next.offset + 8192);
      let written;
      try {
        written = this.output.write(String.fromCharCode(...bytes), bytes.length);
      } catch (error) {
        if (error.result !== Cr.NS_BASE_STREAM_WOULD_BLOCK) {
          throw error;
        }
        written = 0;
      }
      next.offset += written;
      if (next.offset === next.frame.length) {
        this.outgoing.shift();
      } else {
        this.output.asyncWait(this, 0, 0, Services.tm.currentThread);
        return;
      }
    }
  }

  onOutputStreamReady() {
    try { this._flush(); } catch { this.close(); }
  }

  onStartRequest() {}

  onStopRequest() {
    this.close();
  }

  onDataAvailable(_request, input, _offset, count) {
    if (this.closed) {
      return;
    }
    const binary = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
    binary.setInputStream(input);
    try {
      while (count) {
        const length = Math.min(count, 8192);
        this._consume(new Uint8Array(binary.readByteArray(length)));
        count -= length;
      }
    } catch {
      this.close("invalid_response");
    }
  }

  _consume(bytes) {
    let offset = 0;
    while (offset < bytes.length && !this.closed) {
      if (!this.body) {
        const length = Math.min(4 - this.headerUsed, bytes.length - offset);
        this.header.set(bytes.subarray(offset, offset + length), this.headerUsed);
        this.headerUsed += length;
        offset += length;
        if (this.headerUsed !== 4) {
          continue;
        }
        const size = new DataView(this.header.buffer).getUint32(0);
        if (!size || size > MAX_RESPONSE) {
          fail("invalid_response");
        }
        this.body = new Uint8Array(size);
        this.bodyUsed = 0;
      }
      const length = Math.min(this.body.length - this.bodyUsed, bytes.length - offset);
      this.body.set(bytes.subarray(offset, offset + length), this.bodyUsed);
      this.bodyUsed += length;
      offset += length;
      if (this.bodyUsed === this.body.length) {
        const response = JSON.parse(decoder.decode(this.body));
        this.body = null;
        this.headerUsed = 0;
        this._response(response);
      }
    }
  }

  _response(response) {
    if (!response || response.version !== VERSION || typeof response.id !== "string" ||
        !/^[0-9a-f]{32}$/.test(response.id)) {
      fail("invalid_response");
    }
    const pending = this.pending.get(response.id);
    if (!pending) {
      fail("invalid_response");
    }
    if (response.event === "admitted" && pending.type === "submit" && !pending.admitted) {
      pending.admitted = true;
      pending.onAdmitted();
      return;
    }
    let result;
    if (response.event === "capabilities" && pending.type === "capabilities") {
      const caps = response.capabilities;
      if (!caps || caps.visibility !== "public_cooperative" || caps.network_access !== true ||
          caps.private_data_supported !== false || caps.public_cache !== true || caps.training !== false ||
          caps.cloud_fallback !== false || caps.max_question_bytes !== 512 || caps.max_context_bytes !== 4096 ||
          caps.max_request_bytes !== MAX_REQUEST || caps.max_response_bytes !== MAX_RESPONSE ||
          caps.execution_slots !== 1 || caps.max_connections !== 8 ||
          caps.retained_public_receipts !== true || caps.remote_erasure_guaranteed !== false ||
          caps.model_execution_proven !== false || typeof caps.quarantined !== "boolean" ||
          !Number.isInteger(caps.max_seconds) || caps.max_seconds < 1 || caps.max_seconds > 600 ||
          !Number.isInteger(caps.max_task_seconds) || caps.max_task_seconds < 1 ||
          caps.max_task_seconds > 7200 || typeof caps.model_profile !== "string" ||
          !caps.model_profile || caps.model_profile.length > 128) {
        fail("invalid_response");
      }
      result = Object.freeze({ ...caps });
    } else if (response.event === "result" && pending.type === "submit" && pending.admitted) {
      result = response.result;
      const keys = value => Array.isArray(value) && value.length <= 4 &&
        value.every(key => typeof key === "string" && /^[0-9a-f]{64}$/.test(key) && !/^0+$/.test(key)) &&
        new Set(value).size === value.length;
      if (!result || result.cleanup?.complete !== true || result.remote_cleanup_confirmed !== true ||
          result.retained_public_receipts !== true || result.model_answer_correctness_proven !== false ||
          result.semantic_completeness_proven !== false ||
          typeof result.output?.text !== "string" || result.output.text.includes("\0") ||
          typeof result.answer_complete !== "boolean" || typeof result.execution_complete !== "boolean" ||
          !["complete", "incomplete"].includes(result.answer_status) ||
          result.answer_complete !== (result.answer_status === "complete") ||
          !keys(result.provider_keys) || !keys(result.selected_provider_keys) ||
          result.selected_provider_keys.length < 2 ||
          !result.provider_keys.every(key => result.selected_provider_keys.includes(key)) ||
          !["single_source_answer", "hierarchical_peer_synthesis", "hierarchical_peer_synthesis_incomplete",
            "awaiting_fragments_before_peer_synthesis", "incomplete_fragment_answers",
            "ordered_source_ranges_not_neural_synthesis"].includes(result.joining) ||
          !Number.isInteger(result.package_count) || result.package_count < 1 ||
          !Number.isInteger(result.total_parts) || result.total_parts < 1 ||
          !Number.isInteger(result.synthesis_levels) || result.synthesis_levels < 0 ||
          result.synthesis_levels > 16 || !/^[0-9a-f]{64}$/.test(result.source_manifest_id || "") ||
          (result.answer_complete && (!result.execution_complete || !result.output.text.trim() ||
            !result.provider_keys.length ||
            !["single_source_answer", "hierarchical_peer_synthesis"].includes(result.joining)))) {
        fail("invalid_response");
      }
    } else if (response.event === "cancel_requested" && pending.type === "cancel" && response.task_id === pending.taskId) {
      result = { task_id: response.task_id };
    } else if (response.event !== "error" || !errors.has(response.code)) {
      fail("invalid_response");
    }
    this.pending.delete(response.id);
    clearTimeout(pending.timer);
    if (response.id === this.active) {
      this.active = null;
    }
    if (response.event === "error") {
      pending.reject(new VolparossaCooperativeComputeError(response.code));
    } else {
      pending.resolve(result);
    }
  }
}

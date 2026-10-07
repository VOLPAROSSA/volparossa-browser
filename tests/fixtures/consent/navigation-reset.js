// SPDX-License-Identifier: MIT
// Upstream callback below is copied unchanged from Consent-O-Matic v1.1.5:
// https://github.com/cavi-au/Consent-O-Matic/blob/a539a8e06101d53496ac71c2a45abe3f4287ac7c/Extension/background.js#L201
// Copyright (c) 2019,2020,2021,2022 Janus Bager Kristensen and Rolf Bagge,
// CAVI - Center for Advanced Visualization and Interaction, Aarhus University.
// Original license: docs/licenses/Consent-O-Matic-MIT.txt.
// This callback-level regression uses synthetic browser API events. It is not
// evidence that a patched package was signed, installed or accepted upstream.
const upstreamCallback = (tabId, info, tab)=>{
    if(info.status != null && info.status === "Loading") {
        setBadgeCheckmark("", tabId);
        tabStatusMap.set(tabId, STATUS.INIT);
    }
};

const proposedCallback = (tabId, info, tab)=>{
    if(info.status != null && info.status === "loading") {
        setBadgeCheckmark("", tabId);
        tabStatusMap.set(tabId, STATUS.INIT);
    }
};

const STATUS = { INIT: 0, HANDLED: 1 };
const tabStatusMap = new Map();
let badges = [];
function setBadgeCheckmark(text, id) { badges.push({text, id}); }
function reset() { tabStatusMap.clear(); tabStatusMap.set(7, STATUS.HANDLED); badges = []; }
reset();
upstreamCallback(7, {status: "loading"}, {});
const reproduced = tabStatusMap.get(7) === STATUS.HANDLED && badges.length === 0;
reset();
proposedCallback(7, {status: "loading"}, {});
const fixed = tabStatusMap.get(7) === STATUS.INIT && badges.length === 1
  && badges[0].text === "" && badges[0].id === 7;
reset();
for (const event of [{status: "complete"}, {title: "Synthetic title"}, {}]) {
    proposedCallback(7, event, {});
}
const unrelatedPreserved = tabStatusMap.get(7) === STATUS.HANDLED && badges.length === 0;
return {reproduced, fixed, unrelatedPreserved};

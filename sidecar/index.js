const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const express = require('express');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

// ============================================================================
// Configuration
// ============================================================================

const DJANGO_API_URL = process.env.DJANGO_API_URL || 'http://127.0.0.1:8000/shipments/api/ingest/';
// Shared secret with Django (QA-004). Same value as WHATSAPP_INGEST_SECRET on the Django side.
// No default: a value that ships in the repository is public. Refuse to start without a real one.
const DJANGO_SECRET = (process.env.DJANGO_SECRET || '').trim();
const COMPROMISED_SECRETS = new Set(['fila_secret_2026']);
if (DJANGO_SECRET.length < 16 || COMPROMISED_SECRETS.has(DJANGO_SECRET)) {
    console.error('FATAL: DJANGO_SECRET is missing, shorter than 16 characters, or a value that was published in git. ' +
        'Set it to the same random value as WHATSAPP_INGEST_SECRET on the Django service.');
    process.exit(1);
}
const TARGET_GROUP_ID = process.env.TARGET_GROUP_ID || '';
const PORT = process.env.PORT || 3001;

console.log('🚀 WhatsApp Sidecar Starting...');
console.log(`📡 Django API URL: ${DJANGO_API_URL}`);
console.log(`🔐 Using Bearer Token Authentication`);
console.log(`🌐 REST API on port ${PORT}`);
if (TARGET_GROUP_ID) {
    console.log(`📱 Listening to specific group: ${TARGET_GROUP_ID}`);
} else {
    console.log(`📱 Listening to all incoming messages`);
}
console.log('');

// ============================================================================
// Global State
// ============================================================================

let sock = null;
let socketGeneration = 0; // bumped for every socket; events from an older socket are ignored (QA-015)
let startInProgress = false;
let reconnectTimer = null; // at most ONE pending (re)start at any time
let reconnectAttempts = 0;
let qrTimeouts = 0; // consecutive pairing QRs that nobody scanned
let groupsPollTimer = null;
let qrCodeData = null;
let activeGroupJids = new Set(); // Store active group JIDs from Django
// QA-054: the checkbox on the website's WhatsApp page. Off (fail closed) until Django says true: the connected
// account's own messages are then processed like a group member's.
let acceptOwnMessages = false;
let connectionStatus = 'disconnected'; // disconnected, connecting, connected, error
let authStatus = 'not_authenticated'; // not_authenticated, authenticating, authenticated
let lastError = null;
let lastErrorTime = null;
const logs = []; // Circular buffer for logs
const MAX_LOGS = 500;
const groups = new Map(); // Store groups by JID

// ============================================================================
// Logging Utility
// ============================================================================

function addLog(level, message) {
    const timestamp = new Date().toISOString();
    const logEntry = { level, message, timestamp, created_at: timestamp };

    console.log(`[${level.toUpperCase()}] ${message}`);
    logs.push(logEntry);

    // Maintain circular buffer size
    if (logs.length > MAX_LOGS) {
        logs.shift();
    }
}

// A failing background task must never take the process down (QA-015): log it and carry on.
process.on('unhandledRejection', (reason) => {
    addLog('error', `Unhandled rejection: ${reason && reason.message ? reason.message : reason}`);
});
process.on('uncaughtException', (err) => {
    // State may be inconsistent after a synchronous throw: log, then let Docker restart a clean process.
    console.error('💥 Uncaught exception:', err);
    setTimeout(() => process.exit(1), 100);
});

// ============================================================================
// Django API Communication
// ============================================================================

let lastNotified = null;
async function notifyDjangoStatus(status, auth, error = null) {
    // Tell Django only when something changed (QA-045): during a reconnect loop the same state used to be posted
    // over and over, and each post became a database row.
    const signature = JSON.stringify([status, auth, qrCodeData, error]);
    if (signature === lastNotified) return;
    try {
        const payload = {
            sidecar_status: status,
            auth_status: auth,
            qr_code: qrCodeData,
            last_error: error
        };

        await axios.post(
            `${DJANGO_API_URL.replace('/shipments/api/ingest/', '/shipments/api/whatsapp/update-status/')}`,
            payload,
            {
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${DJANGO_SECRET}`
                },
                timeout: 5000
            }
        );
        lastNotified = signature; // remembered only after Django accepted it, so a failed post is retried
    } catch (err) {
        addLog('warning', `Failed to notify Django of status: ${err.message}`);
    }
}

async function sendGroupsToDjango() {
    try {
        const groupsList = Array.from(groups.values());

        await axios.post(
            `${DJANGO_API_URL.replace('/shipments/api/ingest/', '/shipments/api/whatsapp/sync-groups/')}`,
            { groups: groupsList },
            {
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${DJANGO_SECRET}`
                },
                timeout: 5000
            }
        );

        addLog('info', `Synced ${groupsList.length} groups to Django`);
    } catch (err) {
        addLog('warning', `Failed to sync groups to Django: ${err.message}`);
    }
}

// ============================================================================
// WhatsApp connection lifecycle
// ============================================================================

// WhatsApp rejects clients that announce an outdated WhatsApp-Web version with HTTP 405
// ("Connection Failure"). Never hard-code it (QA-001): ask the Baileys project for the current
// one on every (re)connect and fall back to the version bundled with the installed Baileys.
// WA_VERSION="2,3000,1234567890" forces a value (e.g. for diagnosing).
async function resolveWaVersion() {
    if (process.env.WA_VERSION) {
        const parts = process.env.WA_VERSION.split(',').map((n) => parseInt(n.trim(), 10));
        if (parts.length === 3 && parts.every(Number.isFinite)) return parts;
        addLog('warning', 'Ignoring malformed WA_VERSION (expected "2,3000,123456789")');
    }
    try {
        if (typeof fetchLatestBaileysVersion === 'function') {
            const { version, error } = await fetchLatestBaileysVersion();
            if (Array.isArray(version) && !error) return version;
            addLog('warning', `Could not fetch the latest WhatsApp-Web version (${error ? error.message : 'no version'}), using the bundled one`);
        }
    } catch (err) {
        addLog('warning', `Could not fetch the latest WhatsApp-Web version (${err.message}), using the bundled one`);
    }
    return undefined;
}

const AUTH_DIR = () => path.join(process.cwd(), 'auth_data');

function clearReconnectTimer() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
}

// Close the current socket for good. Its listeners stay registered but are ignored (generation check), except
// 'creds.update' which must keep saving. Without this a restart left two live sockets (QA-015).
function teardownSocket() {
    const old = sock;
    sock = null;
    socketGeneration++;
    if (!old) return;
    try {
        old.end(undefined);
    } catch (err) {
        addLog('warning', `Error closing the old socket: ${err.message}`);
    }
}

// Start (or restart) after delayMs. Always replaces a pending timer, so reconnect storms cannot stack up.
function scheduleStart(delayMs, reason) {
    clearReconnectTimer();
    addLog('info', `Starting WhatsApp connection in ${Math.round(delayMs / 1000)}s (${reason})`);
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        startBot().catch((err) => {
            addLog('error', `Start failed: ${err.message}`);
            scheduleStart(backoffMs(3000), 'start failed');
        });
    }, delayMs);
}

const MAX_BACKOFF_MS = 5 * 60 * 1000;
function backoffMs(base, cap = MAX_BACKOFF_MS) {
    const exp = Math.min(cap, base * 2 ** Math.min(reconnectAttempts, 10));
    reconnectAttempts++;
    return Math.round(exp * (0.8 + Math.random() * 0.4)); // +-20 % jitter
}

// What to do after the socket closed, by WhatsApp's status code (QA-015).
function decideReconnect(statusCode) {
    switch (statusCode) {
        case DisconnectReason.loggedOut: // 401: the phone unlinked this device, the saved credentials are dead
            return { delay: 2000, clearAuth: true, reason: 'logged out: saved session is invalid, requesting a new QR' };
        case DisconnectReason.restartRequired: // 515: normal right after pairing
            return { delay: 500, reason: 'restart required by WhatsApp' };
        case DisconnectReason.connectionReplaced: // 440: the same session is open elsewhere; do not fight over it
            return { delay: Math.max(60000, backoffMs(30000)), reason: 'session opened elsewhere (440)' };
        case 405: // client version rejected / blocked: hammering makes it worse
        case 403:
            return { delay: backoffMs(60000, 15 * 60 * 1000), reason: `rejected by WhatsApp (${statusCode})` };
        default:
            return { delay: backoffMs(3000), reason: `connection closed (${statusCode || 'no status'})` };
    }
}

function clearAuthFiles() {
    const authPath = AUTH_DIR();
    fs.mkdirSync(authPath, { recursive: true });
    for (const file of fs.readdirSync(authPath)) {
        if (file.startsWith('_')) continue; // _outbox.json / _active_groups.json are not part of the WhatsApp session
        try {
            fs.rmSync(path.join(authPath, file), { recursive: true, force: true });
        } catch (e) {
            addLog('warning', 'Could not delete: ' + file);
        }
    }
    addLog('info', 'Auth data cleared');
}

async function handleClose(lastDisconnect) {
    connectionStatus = 'disconnected';
    const error = lastDisconnect && lastDisconnect.error;
    const statusCode = error && error.output && error.output.statusCode;

    lastError = `Disconnected: ${statusCode || ''} ${error && error.message ? error.message : ''}`.trim();
    lastErrorTime = new Date();
    addLog('error', lastError);

    let decision;
    if (authStatus === 'authenticating' && statusCode !== DisconnectReason.loggedOut && statusCode !== DisconnectReason.restartRequired) {
        // pairing in progress and nobody scanned the QR in time: that is normal, retry with a short capped delay
        qrTimeouts++;
        decision = { delay: Math.min(60000, 3000 * 2 ** Math.min(qrTimeouts, 5)), reason: 'QR not scanned yet' };
    } else {
        decision = decideReconnect(statusCode);
    }

    if (decision.clearAuth) {
        clearAuthFiles();
        authStatus = 'not_authenticated';
        qrCodeData = null;
    }
    notifyDjangoStatus(connectionStatus, authStatus, lastError);
    scheduleStart(decision.delay, decision.reason);
}

async function onConnectionUpdate(update) {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
        authStatus = 'authenticating';
        qrCodeData = qr;

        console.log('\n📱 QR CODE RECEIVED - Scan with WhatsApp on your phone:\n');
        qrcode.generate(qr, { small: true });
        console.log('\n');

        addLog('info', 'QR code generated, awaiting scan');
        notifyDjangoStatus(connectionStatus, authStatus);
    }

    if (connection === 'close') {
        await handleClose(lastDisconnect);
    } else if (connection === 'open') {
        connectionStatus = 'connected';
        authStatus = 'authenticated';
        qrCodeData = null; // Clear QR code
        reconnectAttempts = 0;
        qrTimeouts = 0;
        lastError = null;

        console.log('✅ Opened connection, ready to receive WhatsApp messages!');
        addLog('info', 'Connected and authenticated to WhatsApp');
        notifyDjangoStatus(connectionStatus, authStatus);

        // Fetch and cache groups
        await fetchGroups();
        startGroupsPolling();
    }
}

// ONE poll timer for the whole process (it used to be created again on every 'open' and never stopped).
function startGroupsPolling() {
    if (groupsPollTimer) return;
    groupsPollTimer = setInterval(async () => {
        if (connectionStatus === 'connected') {
            await fetchActiveGroupsFromDjango();
        }
    }, 30000);
}

async function startBot() {
    if (startInProgress) return;
    startInProgress = true;
    try {
        clearReconnectTimer();
        teardownSocket(); // never more than one live socket
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR());

        const socketConfig = {
            auth: state,
            browser: ['Fila Trucking', 'Safari', '15.0'],
            printQRInTerminal: false,
        };
        const waVersion = await resolveWaVersion();
        if (waVersion) {
            socketConfig.version = waVersion; // only set when known: an explicit undefined would override Baileys' default
            addLog('info', `Using WhatsApp-Web version ${waVersion.join('.')}`);
        }
        connectionStatus = 'connecting';
        const mySocket = makeWASocket(socketConfig);
        sock = mySocket;
        const myGeneration = socketGeneration;
        const isCurrent = () => myGeneration === socketGeneration;

        mySocket.ev.on('creds.update', saveCreds);
        mySocket.ev.on('connection.update', async (update) => {
            if (!isCurrent()) return; // a replaced socket must not drive the state machine
            try {
                await onConnectionUpdate(update);
            } catch (err) {
                addLog('error', `connection.update handler failed: ${err.message}`);
            }
        });
        attachMessageHandlers(mySocket, isCurrent);
    } finally {
        startInProgress = false;
    }
}

// ============================================================================
// Incoming WhatsApp events
// ============================================================================

// sendMessage on a closed socket rejects; an unhandled rejection used to kill the process (QA-015).
// What the bot itself sent (QA-054). Its replies are fromMe messages too: once the connected account's own messages
// are accepted, they must never be fed back to Django (answer -> parse -> answer ...). The text is remembered BEFORE
// sending (an echo can arrive before sendMessage resolves) and the id after; both sets are bounded.
const SENT_MEMORY = 500;
const sentTexts = new Set();
const sentIds = new Set();
function remember(set, value) {
    if (!value) return;
    set.add(value);
    if (set.size > SENT_MEMORY) set.delete(set.values().next().value);
}
function isOwnReply(msg, text) {
    return sentIds.has(msg.key.id) || sentTexts.has(text);
}
// '9715550100:12@s.whatsapp.net' (account id with the device number) -> '9715550100@s.whatsapp.net'
function ownJid() {
    const id = sock && sock.user && sock.user.id;
    if (!id) return null;
    const [user, domain] = String(id).split('@');
    return `${user.split(':')[0]}@${domain || 's.whatsapp.net'}`;
}

async function safeSend(jid, content) {
    try {
        if (!sock) throw new Error('not connected');
        remember(sentTexts, content && content.text);
        const sent = await sock.sendMessage(jid, content);
        remember(sentIds, sent && sent.key && sent.key.id);
        return true;
    } catch (err) {
        addLog('warning', `Could not send a WhatsApp message to ${jid}: ${err.message}`);
        return false;
    }
}

// Replies go out one at a time, REPLY_GAP_MS apart (default 3 s = at most 20 a minute): a bot that answers in bursts
// looks like a spammer to WhatsApp (account-ban risk), but every driver still gets an answer (QA-016).
const REPLY_GAP_MS = parseInt(process.env.REPLY_GAP_MS || '3000', 10);
const REPLY_QUEUE_MAX = 100;
const replyQueue = [];
let replyPumping = false;

function reply(jid, text) {
    if (replyQueue.length >= REPLY_QUEUE_MAX) {
        addLog('warning', `Reply to ${jid} dropped (reply queue full)`);
        return false;
    }
    replyQueue.push({ jid, text });
    pumpReplies().catch((err) => addLog('error', `Reply pump failed: ${err.message}`));
    return true;
}

async function pumpReplies() {
    if (replyPumping) return;
    replyPumping = true;
    try {
        while (replyQueue.length) {
            const { jid, text } = replyQueue.shift();
            await safeSend(jid, { text });
            if (replyQueue.length) await new Promise((resolve) => setTimeout(resolve, REPLY_GAP_MS));
        }
    } finally {
        replyPumping = false;
    }
}

function attachMessageHandlers(socket, isCurrent) {
    socket.ev.on('groups.upsert', async (groupsUpserted) => {
        if (!isCurrent()) return;
        for (const group of groupsUpserted) {
            groups.set(group.id, {
                jid: group.id,
                name: group.subject || 'Unknown Group',
                participants: group.participants ? group.participants.length : 0
            });
        }
        addLog('info', `Updated ${groupsUpserted.length} group(s)`);
        await sendGroupsToDjango();
    });

    socket.ev.on('messages.upsert', async (m) => {
        if (!isCurrent()) return;
        if (m.type !== 'notify') return;

        for (const msg of m.messages) {
            try {
                await handleIncomingMessage(msg);
            } catch (err) {
                addLog('error', `Could not process a message: ${err.message}`);
            }
        }
    });
}

async function handleIncomingMessage(msg) {
    if (!msg.message) return;

    const messageContent = msg.message.conversation || msg.message.extendedTextMessage?.text;
    if (!messageContent) return;

    // The connected account's own messages (QA-054): only when the admin ticked the box, and never the bot's replies.
    const own = Boolean(msg.key.fromMe);
    if (own && (!acceptOwnMessages || isOwnReply(msg, messageContent))) return;

    // Fail closed (QA-020): only messages from groups the admin explicitly enabled are processed. Until the list
    // has been loaded (from Django, or the last good copy on disk) and whenever it is empty, NOTHING is processed,
    // instead of "listen and answer everywhere" as before.
    const msgGroupJid = msg.key.remoteJid;
    const isGroupMessage = msgGroupJid && msgGroupJid.endsWith('@g.us');

    if (isGroupMessage) {
        if (!activeGroupJids.has(msgGroupJid)) return;
    } else if (!(TARGET_GROUP_ID && msgGroupJid === TARGET_GROUP_ID)) {
        return; // 1:1 chats, broadcasts and status updates are never dispatch messages
    }

    // For the account's own messages the participant is not reliable and the fallback would be the group id (which
    // matches no driver): use the account's own number, so a driver record with that phone is assigned as usual.
    const sender = (own && ownJid()) || msg.key.participant || msg.key.remoteJid;

    // Always send message to Django for processing
    addLog('info', `Received message from ${sender}${own ? ' (connected account)' : ''}: ${messageContent.substring(0, 50)}...`);

    const payload = {
        text: messageContent,
        sender: sender,
        // WhatsApp's own message id lets Django ignore the same message delivered twice (reconnect replays, retries)
        message_id: msg.key.id,
        timestamp: msg.messageTimestamp ? new Date(msg.messageTimestamp * 1000).toISOString() : new Date().toISOString()
    };
    const entry = { payload, jid: msg.key.remoteJid, attempts: 0, firstAttemptAt: Date.now(), nextAttemptAt: 0 };
    await deliver(entry, true);
}

// ----------------------------------------------------------------------------
// Delivery to Django with a small persistent retry queue (QA-018)
// ----------------------------------------------------------------------------
// Before: one POST with a 10 s timeout; if Django was restarting, the driver's message was lost for good.
// Now: network errors / 5xx / 429 are queued (on disk, so a sidecar restart does not lose them) and retried with
// backoff for up to 24 h. 4xx answers are final (the message itself is the problem) and are not retried.

const OUTBOX_INTERVAL_MS = parseInt(process.env.OUTBOX_INTERVAL_MS || '15000', 10);
const OUTBOX_RETRY_BASE_MS = parseInt(process.env.OUTBOX_RETRY_BASE_MS || '15000', 10);
const OUTBOX_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const OUTBOX_MAX_ITEMS = 200;
const OUTBOX_FILE = () => path.join(AUTH_DIR(), '_outbox.json'); // inside the persistent volume; kept when the session is cleared
let outbox = [];
let outboxTimer = null;
let outboxBusy = false;

function loadOutbox() {
    try {
        const parsed = JSON.parse(fs.readFileSync(OUTBOX_FILE(), 'utf8'));
        if (Array.isArray(parsed)) outbox = parsed.filter((e) => e && e.payload && e.payload.text);
        if (outbox.length) addLog('info', `Restored ${outbox.length} unsent message(s) from disk`);
    } catch (err) {
        if (err.code !== 'ENOENT') addLog('warning', `Could not read the retry queue: ${err.message}`);
    }
}

function saveOutbox() {
    try {
        fs.mkdirSync(AUTH_DIR(), { recursive: true });
        const tmp = OUTBOX_FILE() + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(outbox));
        fs.renameSync(tmp, OUTBOX_FILE());
    } catch (err) {
        addLog('warning', `Could not save the retry queue: ${err.message}`);
    }
}

function isRetryable(error) {
    const status = error.response && error.response.status;
    return !status || status >= 500 || status === 429; // no answer at all, server trouble, or throttling
}

function enqueue(entry) {
    entry.attempts += 1;
    entry.nextAttemptAt = Date.now() + Math.min(MAX_BACKOFF_MS, OUTBOX_RETRY_BASE_MS * 2 ** Math.min(entry.attempts - 1, 8));
    if (!outbox.includes(entry)) outbox.push(entry);
    while (outbox.length > OUTBOX_MAX_ITEMS) {
        const dropped = outbox.shift();
        addLog('error', `Retry queue full: dropped the oldest message (${dropped.payload.message_id || 'no id'})`);
    }
    saveOutbox();
}

// One delivery attempt. Returns true when the message is settled (answered or given up), false to retry later.
async function deliver(entry, firstTry) {
    try {
        const response = await axios.post(DJANGO_API_URL, entry.payload, {
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${DJANGO_SECRET}`
            },
            timeout: 10000
        });
        const data = response.data || {};
        addLog('info', `API response: ${data.status}`);

        // Only answer when Django asks us to (a shipment was recorded, or an obvious attempt needs fixing).
        // 'ignored' (chit-chat) and 'duplicate' and anything unknown stay silent (QA-016).
        if (data.reply_message && (data.status === 'success' || data.status === 'flagged')) {
            await reply(entry.jid, data.reply_message);
        } else if (data.status === 'success') {
            let replyText = `✅ Shipment processed successfully (ID: ${data.shipment_id}). Status: Pending confirmation.`;
            if (data.flagged) {
                replyText += '\n⚠️ Note: System flagged a potential duplicate container. Admin will review.';
            }
            await reply(entry.jid, replyText);
        }
        return true;
    } catch (error) {
        // Django down / rejected the request. This is not the driver's fault: log it, never answer with
        // "please use the correct format" (that spammed the group on every chat message while Django was down).
        const errorMsg = error.response && error.response.data && error.response.data.message
            ? error.response.data.message
            : error.message;
        if (isRetryable(error)) {
            addLog('error', `Django API error (will retry): ${errorMsg}`);
            if (firstTry || outbox.includes(entry)) {
                if (Date.now() - entry.firstAttemptAt > OUTBOX_MAX_AGE_MS) {
                    addLog('error', 'Giving up on a message older than 24 h');
                    return true;
                }
                enqueue(entry);
            }
            return false;
        }
        addLog('error', `Django API error (not retried): ${errorMsg}`);
        return true;
    }
}

async function processOutbox() {
    if (outboxBusy) return;
    outboxBusy = true;
    try {
        const due = outbox.filter((e) => e.nextAttemptAt <= Date.now());
        for (const entry of due) {
            const settled = await deliver(entry, false);
            if (settled) {
                outbox = outbox.filter((e) => e !== entry);
                saveOutbox();
            }
        }
    } finally {
        outboxBusy = false;
    }
}

function startOutbox() {
    loadActiveGroupsCache();
    loadOutbox();
    if (!outboxTimer) outboxTimer = setInterval(() => processOutbox().catch((e) => addLog('error', `Retry queue failed: ${e.message}`)), OUTBOX_INTERVAL_MS);
}

async function fetchGroups() {
    try {
        const allGroups = await sock.groupFetchAllParticipating();

        for (const [jid, data] of Object.entries(allGroups)) {
            groups.set(jid, {
                jid: jid,
                name: data.subject || 'Unknown Group',
                participants: data.participants ? data.participants.length : 0
            });
        }

        addLog('info', `Fetched ${groups.size} WhatsApp group(s)`);
        await sendGroupsToDjango();

        // After sending groups, fetch which ones are ACTIVE from Django
        await fetchActiveGroupsFromDjango();
    } catch (err) {
        addLog('warning', `Failed to fetch groups: ${err.message}`);
    }
}

// Active (enabled) groups: Django is the source of truth; the last good answer is cached on disk so a sidecar
// restart while Django is down does not turn the bot deaf (or, worse, open).
const ACTIVE_GROUPS_FILE = () => path.join(AUTH_DIR(), '_active_groups.json');

function loadActiveGroupsCache() {
    try {
        const cached = JSON.parse(fs.readFileSync(ACTIVE_GROUPS_FILE(), 'utf8'));
        // current format {groups: [...], accept_own_messages: bool}; the old one was a plain array (-> option off)
        const list = Array.isArray(cached) ? cached : (cached && cached.groups);
        if (Array.isArray(list)) {
            activeGroupJids = new Set(list.filter((j) => typeof j === 'string'));
            acceptOwnMessages = !Array.isArray(cached) && cached.accept_own_messages === true;
            addLog('info', `Loaded ${activeGroupJids.size} enabled group(s) from the local cache`);
        }
    } catch (err) {
        if (err.code !== 'ENOENT') addLog('warning', `Could not read the enabled-groups cache: ${err.message}`);
    }
}

function saveActiveGroupsCache() {
    try {
        fs.mkdirSync(AUTH_DIR(), { recursive: true });
        fs.writeFileSync(ACTIVE_GROUPS_FILE(), JSON.stringify({ groups: [...activeGroupJids], accept_own_messages: acceptOwnMessages }));
    } catch (err) {
        addLog('warning', `Could not save the enabled-groups cache: ${err.message}`);
    }
}

// Fetch active (checked) groups from Django
async function fetchActiveGroupsFromDjango() {
    try {
        // DJANGO_API_URL is like: http://host.docker.internal:8000/shipments/api/ingest/
        // We need: http://host.docker.internal:8000/shipments/api/whatsapp/groups/
        const djangoUrl = DJANGO_API_URL.replace('ingest', 'whatsapp/groups');
        const response = await axios.get(djangoUrl, {
            headers: { 'Authorization': `Bearer ${DJANGO_SECRET}` },
            timeout: 5000
        });
        if (!response.data || !Array.isArray(response.data.groups)) {
            throw new Error('unexpected answer from Django');
        }

        const fresh = new Set();
        for (const group of response.data.groups) {
            if (group.is_active) fresh.add(group.group_jid);
        }
        activeGroupJids = fresh;
        acceptOwnMessages = response.data.accept_own_messages === true; // anything else = off
        saveActiveGroupsCache();
        addLog('info', `Active groups: ${activeGroupJids.size} enabled for listening` +
            (acceptOwnMessages ? '; messages from the connected account are accepted' : ''));
    } catch (err) {
        // keep the last known list (never "assume all groups are active", which is what this used to do)
        addLog('warning', `Could not fetch active groups, keeping the last known list (${activeGroupJids.size}): ${err.message}`);
    }
}

// ============================================================================
// Express REST API Server
// ============================================================================

const app = express();

// QA-002: every route except the bare /health liveness probe needs the shared secret.
// (Django already sends 'Authorization: Bearer <secret>' on its control calls.)
const SECRET_DIGEST = crypto.createHash('sha256').update(DJANGO_SECRET).digest();
function requireSharedSecret(req, res, next) {
    if (req.method === 'GET' && req.path === '/health') return next();
    const match = /^Bearer (.+)$/.exec(req.get('authorization') || '');
    const supplied = crypto.createHash('sha256').update(match ? match[1] : '').digest();
    if (match && crypto.timingSafeEqual(supplied, SECRET_DIGEST)) return next();
    return res.status(401).json({ error: 'Unauthorized' });
}
app.use(requireSharedSecret);
app.use(express.json());

// GET /api/status - Connection status
app.get('/api/status', (req, res) => {
    res.json({
        sidecar_status: connectionStatus,
        auth_status: authStatus,
        last_connection_time: connectionStatus === 'connected' ? new Date().toISOString() : null,
        last_error: lastError,
        last_error_time: lastErrorTime ? lastErrorTime.toISOString() : null
    });
});

// GET /api/qr-code - Latest QR code
app.get('/api/qr-code', (req, res) => {
    res.json({
        qr_code: qrCodeData,
        auth_status: authStatus
    });
});

// GET /api/logs - Recent logs
app.get('/api/logs', (req, res) => {
    const limit = parseInt(req.query.limit) || 100;
    const offset = parseInt(req.query.offset) || 0;

    const paginatedLogs = logs.slice(offset, offset + limit);

    res.json({
        logs: paginatedLogs,
        total: logs.length,
        offset: offset,
        limit: limit
    });
});

// GET /api/groups - List groups
app.get('/api/groups', (req, res) => {
    const groupsList = Array.from(groups.values());

    res.json({
        groups: groupsList,
        total: groupsList.length
    });
});

// POST /api/restart - Restart connection
app.post('/api/restart', (req, res) => {
    addLog('info', 'Restart requested via API');
    reconnectAttempts = 0;
    qrTimeouts = 0;
    teardownSocket(); // close the old socket first: restarting must never leave two of them open
    scheduleStart(1000, 'restart requested via API');

    res.json({ status: 'restarting' });
});

// DELETE /api/auth - Clear auth and rescan QR
app.delete('/api/auth', (req, res) => {
    addLog('info', 'Auth clear requested via API');

    clearReconnectTimer();
    teardownSocket();
    connectionStatus = 'disconnected';
    authStatus = 'not_authenticated';
    qrCodeData = null;

    try {
        clearAuthFiles();
    } catch (e) {
        addLog('error', 'Warning during auth reset: ' + e.message);
        // Continue anyway - the bot will restart with fresh auth
    }

    reconnectAttempts = 0;
    qrTimeouts = 0;
    scheduleStart(1000, 'auth cleared via API');

    res.json({ status: 'auth_cleared' });
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok' });
});

// ============================================================================
// Startup
// ============================================================================

startOutbox();
startBot().catch(err => {
    console.error('💥 Fatal error starting bot:', err);
    addLog('error', `Fatal error: ${err.message}`);
    connectionStatus = 'error';
    lastError = err.message;
    lastErrorTime = new Date();
    scheduleStart(backoffMs(3000), 'initial start failed');
});

const server = app.listen(PORT, () => {
    console.log(`✅ REST API server listening on port ${PORT}`);
    addLog('info', `REST API server started on port ${PORT}`);
});

// Graceful shutdown
function shutdown() {
    console.log('\n🛑 Shutting down...');
    clearReconnectTimer();
    if (groupsPollTimer) clearInterval(groupsPollTimer);
    if (outboxTimer) clearInterval(outboxTimer);
    teardownSocket();
    server.close();
    process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

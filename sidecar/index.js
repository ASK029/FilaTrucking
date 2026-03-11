const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const express = require('express');

// ============================================================================
// Configuration
// ============================================================================

const DJANGO_API_URL = process.env.DJANGO_API_URL || 'http://127.0.0.1:8000/shipments/api/ingest/';
const DJANGO_SECRET = process.env.DJANGO_SECRET || 'fila_secret_2026';
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
let shouldReconnect = true;
let qrCodeData = null;
let activeGroupJids = new Set(); // Store active group JIDs from Django
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

// ============================================================================
// Django API Communication
// ============================================================================

async function notifyDjangoStatus(status, auth, error = null) {
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
// WhatsApp Bot
// ============================================================================

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');

    sock = makeWASocket({
        auth: state,
        version: [2, 3000, 1034074495],
        browser: ['Fila Trucking', 'Safari', '15.0'],
        printQRInTerminal: false,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
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
            connectionStatus = 'disconnected';
            const wasLoggedOut = lastDisconnect.error?.output?.statusCode === DisconnectReason.loggedOut;
            shouldReconnect = shouldReconnect && !wasLoggedOut;
            
            lastError = `Disconnected: ${JSON.stringify(lastDisconnect.error)}`;
            lastErrorTime = new Date();
            
            addLog('error', lastError);
            notifyDjangoStatus(connectionStatus, authStatus, lastError);
            
            console.log('❌ Connection closed due to', lastDisconnect.error, ', reconnecting', shouldReconnect);
            if (shouldReconnect) {
                setTimeout(() => startBot(), 3000);
            } else {
                console.log('🚪 Logged out. Please delete auth_info_baileys folder and restart to scan new QR code.');
                addLog('error', 'Logged out - auth credentials invalid');
                connectionStatus = 'error';
                authStatus = 'not_authenticated';
                notifyDjangoStatus(connectionStatus, authStatus, 'Logged out');
            }
        } else if (connection === 'open') {
            connectionStatus = 'connected';
            authStatus = 'authenticated';
            qrCodeData = null; // Clear QR code
            
            console.log('✅ Opened connection, ready to receive WhatsApp messages!');
            addLog('info', 'Connected and authenticated to WhatsApp');
            notifyDjangoStatus(connectionStatus, authStatus);
            
            // Fetch and cache groups
            await fetchGroups();
            
            // Periodically refresh active groups every 30 seconds
            setInterval(async () => {
                if (connectionStatus === 'connected') {
                    await fetchActiveGroupsFromDjango();
                }
            }, 30000);
        }
    });

    sock.ev.on('groups.upsert', async (groupsUpserted) => {
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

    sock.ev.on('messages.upsert', async (m) => {
        if (m.type !== 'notify') return;

        for (const msg of m.messages) {
            if (!msg.message || msg.key.fromMe) continue;

            const messageContent = msg.message.conversation || msg.message.extendedTextMessage?.text;
            if (!messageContent) continue;

            // Check if message is from an active group (if any groups are marked active)
            const msgGroupJid = msg.key.remoteJid;
            const isGroupMessage = msgGroupJid && msgGroupJid.endsWith('@g.us');
            
            if (isGroupMessage) {
                // If we have active groups configured, only listen to those
                if (activeGroupJids.size > 0 && !activeGroupJids.has(msgGroupJid)) {
                    continue; // Skip messages from inactive groups
                }
                // If no active groups configured (empty set), listen to all groups
            } else if (TARGET_GROUP_ID) {
                // Legacy: only listen to specific group for individual chats
                if (msg.key.remoteJid !== TARGET_GROUP_ID) continue;
            }

            const sender = msg.key.participant || msg.key.remoteJid;

            if (messageContent.toLowerCase().includes('booking:') || messageContent.toLowerCase().includes('container:')) {
                addLog('info', `Received shipment payload from ${sender}`);

                try {
                    const response = await axios.post(DJANGO_API_URL, {
                        text: messageContent,
                        sender: sender
                    }, {
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${DJANGO_SECRET}`
                        },
                        timeout: 10000
                    });

                    addLog('info', `API response: ${response.data.status}`);
                    
                    if (response.data.status === 'success') {
                        let replyText = `✅ Shipment processed successfully (ID: ${response.data.shipment_id}). Status: Pending confirmation.`;
                        if (response.data.flagged) {
                            replyText += `\n⚠️ Note: System flagged a potential duplicate container. Admin will review.`;
                        }
                        await sock.sendMessage(msg.key.remoteJid, { text: replyText });
                    } else if (response.data.status === 'flagged') {
                        await sock.sendMessage(msg.key.remoteJid, { text: `⚠️ System flagged the shipment: ${response.data.message}` });
                    }

                } catch (error) {
                    const errorMsg = error.response && error.response.data && error.response.data.message 
                        ? error.response.data.message 
                        : error.message;
                    
                    addLog('error', `Django API error: ${errorMsg}`);
                    await sock.sendMessage(msg.key.remoteJid, { text: `❌ Could not process payload: ${errorMsg}` });
                }
            }
        }
    });
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
        
        activeGroupJids.clear();
        
        if (response.data && response.data.groups) {
            for (const group of response.data.groups) {
                if (group.is_active) {
                    activeGroupJids.add(group.group_jid);
                }
            }
        }
        
        addLog('info', `Active groups: ${activeGroupJids.size} enabled for listening`);
    } catch (err) {
        addLog('warning', `Could not fetch active groups: ${err.message}`);
        // If we can't fetch, assume ALL groups are active
        activeGroupJids.clear();
    }
}

// ============================================================================
// Express REST API Server
// ============================================================================

const app = express();
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
    if (sock) {
        sock.end();
    }
    setTimeout(() => {
        startBot();
    }, 1000);
    
    res.json({ status: 'restarting' });
});

// DELETE /api/auth - Clear auth and rescan QR
app.delete('/api/auth', async (req, res) => {
    const fs = require('fs');
    const path = require('path');
    
    addLog('info', 'Auth clear requested via API');
    
    // Force close the sock and prevent reconnect
    const previousReconnect = shouldReconnect;
    shouldReconnect = false;
    
    if (sock) {
        try {
            if (sock.ws) {
                sock.ws.close();
            }
            sock.end?.();
            addLog('info', 'Sock closed');
        } catch (e) {
            addLog('warn', 'Error closing sock: ' + e.message);
        }
        sock = null;
    }
    
    // Wait for connections to fully close
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    const authPath = path.join(process.cwd(), 'auth_info_baileys');
    
    try {
        // Try to clear the auth folder
        if (fs.existsSync(authPath)) {
            try {
                // First try to rename
                const backupPath = path.join(process.cwd(), 'auth_info_baileys_backup_' + Date.now());
                fs.renameSync(authPath, backupPath);
                addLog('info', 'Auth folder renamed to backup');
            } catch (renameErr) {
                addLog('warn', 'Could not rename auth folder: ' + renameErr.message);
                // Continue anyway - we'll create a new folder
            }
        }
        
        // Create fresh auth folder (overwrite if exists)
        if (!fs.existsSync(authPath)) {
            fs.mkdirSync(authPath, { recursive: true });
        }
        addLog('info', 'Auth folder prepared for new login');
        
    } catch (e) {
        addLog('error', 'Warning during auth reset: ' + e.message);
        // Continue anyway - the bot will restart with fresh auth
    }
    
    // Restore reconnect and restart with fresh auth
    shouldReconnect = true;
    setTimeout(() => {
        startBot();
    }, 1000);
    
    res.json({ status: 'auth_cleared' });
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok' });
});

// ============================================================================
// Startup
// ============================================================================

startBot().catch(err => {
    console.error('💥 Fatal error starting bot:', err);
    addLog('error', `Fatal error: ${err.message}`);
    connectionStatus = 'error';
    lastError = err.message;
    lastErrorTime = new Date();
});

const server = app.listen(PORT, () => {
    console.log(`✅ REST API server listening on port ${PORT}`);
    addLog('info', `REST API server started on port ${PORT}`);
});

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\n🛑 Shutting down...');
    if (sock) {
        sock.end();
    }
    server.close();
    process.exit(0);
});

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

// Django API endpoint and matching secret key
const DJANGO_API_URL = 'http://127.0.0.1:8000/shipments/api/ingest/';
const DJANGO_SECRET = 'fila_secret_2026';

// The WhatsApp group or chat we want to listen to (optional filter)
// Leave empty to listen to any incoming messages
const TARGET_GROUP_ID = '';

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');

    const sock = makeWASocket({
        auth: state,
        printQRInTerminal: true,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr) {
            console.log('QR code received, scan to log in');
            // QR is automatically printed to terminal via printQRInTerminal:true config
        }
        
        if (connection === 'close') {
            const shouldReconnect = lastDisconnect.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Connection closed due to', lastDisconnect.error, ', reconnecting', shouldReconnect);
            if (shouldReconnect) {
                startBot();
            } else {
                console.log('Logged out. Please delete auth_info_baileys folder and restart to scan new QR code.');
            }
        } else if (connection === 'open') {
            console.log('Opened connection, ready to receive WhatsApp messages!');
        }
    });

    sock.ev.on('messages.upsert', async (m) => {
        if (m.type !== 'notify') return;

        for (const msg of m.messages) {
            if (!msg.message || msg.key.fromMe) continue;

            // Extract text body
            const messageContent = msg.message.conversation || msg.message.extendedTextMessage?.text;
            if (!messageContent) continue;

            // Optional: Filter by specific group
            if (TARGET_GROUP_ID && msg.key.remoteJid !== TARGET_GROUP_ID) continue;

            const sender = msg.key.participant || msg.key.remoteJid;

            // Basic check if it looks like our structured payload
            if (messageContent.toLowerCase().includes('booking:') || messageContent.toLowerCase().includes('container:')) {
                console.log(`[+] Received potential shipment payload from ${sender}...`);

                try {
                    const response = await axios.post(DJANGO_API_URL, {
                        text: messageContent,
                        sender: sender
                    }, {
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${DJANGO_SECRET}`
                        }
                    });

                    console.log(`[API RESPONSE]`, response.data);
                    
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
                    console.error('[!] Error sending payload to Django:', error.response ? error.response.data : error.message);
                    const errorMsg = error.response && error.response.data && error.response.data.message 
                        ? error.response.data.message 
                        : "Failed to process the shipment due to an internal error.";
                    await sock.sendMessage(msg.key.remoteJid, { text: `❌ Could not process payload: ${errorMsg}` });
                }
            }
        }
    });
}

startBot();

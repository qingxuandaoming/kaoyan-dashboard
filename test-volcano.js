const WebSocket = require('ws');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');
const { Buffer } = require('buffer');

/**
 * Event type definitions
 */
const EventType = {
  None: 0,
  StartConnection: 1,
  FinishConnection: 2,
  ConnectionStarted: 50,
  ConnectionFailed: 51,
  ConnectionFinished: 52,
  StartSession: 100,
  CancelSession: 101,
  FinishSession: 102,
  SessionStarted: 150,
  SessionCanceled: 151,
  SessionFinished: 152,
  SessionFailed: 153,
  UsageResponse: 154,
  TaskRequest: 200,
  UpdateConfig: 201,
  AudioMuted: 250,
  SayHello: 300,
  TTSSentenceStart: 350,
  TTSSentenceEnd: 351,
  TTSResponse: 352,
  TTSEnded: 359,
  PodcastRoundStart: 360,
  PodcastRoundResponse: 361,
  PodcastRoundEnd: 362,
  ASRInfo: 450,
  ASRResponse: 451,
  ASREnded: 459,
  ChatTTSText: 500,
  ChatResponse: 550,
  ChatEnded: 559,
  SourceSubtitleStart: 650,
  SourceSubtitleResponse: 651,
  SourceSubtitleEnd: 652,
  TranslationSubtitleStart: 653,
  TranslationSubtitleResponse: 654,
  TranslationSubtitleEnd: 655,
};

const EventTypeNames = {};
for (const [key, value] of Object.entries(EventType)) {
  EventTypeNames[value] = key;
}

const MsgType = {
  Invalid: 0,
  FullClientRequest: 0b1,
  AudioOnlyClient: 0b10,
  FullServerResponse: 0b1001,
  AudioOnlyServer: 0b1011,
  FrontEndResultServer: 0b1100,
  Error: 0b1111,
};

const MsgTypeNames = {};
for (const [key, value] of Object.entries(MsgType)) {
  MsgTypeNames[value] = key;
}

const MsgTypeFlagBits = {
  NoSeq: 0,
  PositiveSeq: 0b1,
  LastNoSeq: 0b10,
  NegativeSeq: 0b11,
  WithEvent: 0b100,
};

const VersionBits = {
  Version1: 1,
  Version2: 2,
  Version3: 3,
  Version4: 4,
};

const HeaderSizeBits = {
  HeaderSize4: 1,
  HeaderSize8: 2,
  HeaderSize12: 3,
  HeaderSize16: 4,
};

const SerializationBits = {
  Raw: 0,
  JSON: 0b1,
  Thrift: 0b11,
  Custom: 0b1111,
};

const CompressionBits = {
  None: 0,
  Gzip: 0b1,
  Custom: 0b1111,
};

function getEventTypeName(eventType) {
  return EventTypeNames[eventType] || `invalid event type: ${eventType}`;
}

function getMsgTypeName(msgType) {
  return MsgTypeNames[msgType] || `invalid message type: ${msgType}`;
}

function messageToString(msg) {
  const eventStr = msg.event !== undefined ? getEventTypeName(msg.event) : 'NoEvent';
  const typeStr = getMsgTypeName(msg.type);

  switch (msg.type) {
    case MsgType.AudioOnlyServer:
    case MsgType.AudioOnlyClient:
      if (msg.flag === MsgTypeFlagBits.PositiveSeq || msg.flag === MsgTypeFlagBits.NegativeSeq) {
        return `MsgType: ${typeStr}, EventType: ${eventStr}, Sequence: ${msg.sequence}, PayloadSize: ${msg.payload.length}`;
      }
      return `MsgType: ${typeStr}, EventType: ${eventStr}, PayloadSize: ${msg.payload.length}`;

    case MsgType.Error:
      return `MsgType: ${typeStr}, EventType: ${eventStr}, ErrorCode: ${msg.errorCode}, Payload: ${new TextDecoder().decode(msg.payload)}`;

    default:
      if (msg.flag === MsgTypeFlagBits.PositiveSeq || msg.flag === MsgTypeFlagBits.NegativeSeq) {
        return `MsgType: ${typeStr}, EventType: ${eventStr}, Sequence: ${msg.sequence}, Payload: ${new TextDecoder().decode(msg.payload)}`;
      }
      return `MsgType: ${typeStr}, EventType: ${eventStr}, Payload: ${new TextDecoder().decode(msg.payload)}`;
  }
}

function createMessage(msgType, flag) {
  const msg = {
    type: msgType,
    flag: flag,
    version: VersionBits.Version1,
    headerSize: HeaderSizeBits.HeaderSize4,
    serialization: SerializationBits.JSON,
    compression: CompressionBits.None,
    payload: new Uint8Array(0),
  };

  Object.defineProperty(msg, 'toString', {
    enumerable: false,
    configurable: true,
    writable: true,
    value: function () {
      return messageToString(this);
    },
  });

  return msg;
}

function marshalMessage(msg) {
  const buffers = [];

  const headerSize = 4 * msg.headerSize;
  const header = new Uint8Array(headerSize);

  header[0] = (msg.version << 4) | msg.headerSize;
  header[1] = (msg.type << 4) | msg.flag;
  header[2] = (msg.serialization << 4) | msg.compression;

  buffers.push(header);

  const writers = getWriters(msg);
  for (const writer of writers) {
    const data = writer(msg);
    if (data) buffers.push(data);
  }

  const totalLength = buffers.reduce((sum, buf) => sum + buf.length, 0);
  const result = new Uint8Array(totalLength);
  let offset = 0;

  for (const buf of buffers) {
    result.set(buf, offset);
    offset += buf.length;
  }

  return result;
}

function unmarshalMessage(data) {
  if (data.length < 3) {
    throw new Error(`data too short: expected at least 3 bytes, got ${data.length}`);
  }

  let offset = 0;

  const versionAndHeaderSize = data[offset++];
  const typeAndFlag = data[offset++];
  const serializationAndCompression = data[offset++];

  const msg = {
    version: versionAndHeaderSize >> 4,
    headerSize: versionAndHeaderSize & 0b00001111,
    type: typeAndFlag >> 4,
    flag: typeAndFlag & 0b00001111,
    serialization: serializationAndCompression >> 4,
    compression: serializationAndCompression & 0b00001111,
    payload: new Uint8Array(0),
  };

  Object.defineProperty(msg, 'toString', {
    enumerable: false,
    configurable: true,
    writable: true,
    value: function () {
      return messageToString(this);
    },
  });

  offset = 4 * msg.headerSize;

  const readers = getReaders(msg);
  for (const reader of readers) {
    offset = reader(msg, data, offset);
  }

  return msg;
}

function getWriters(msg) {
  const writers = [];

  if (msg.flag === MsgTypeFlagBits.WithEvent) {
    writers.push(writeEvent, writeSessionId);
  }

  switch (msg.type) {
    case MsgType.AudioOnlyClient:
    case MsgType.AudioOnlyServer:
    case MsgType.FrontEndResultServer:
    case MsgType.FullClientRequest:
    case MsgType.FullServerResponse:
      if (msg.flag === MsgTypeFlagBits.PositiveSeq || msg.flag === MsgTypeFlagBits.NegativeSeq) {
        writers.push(writeSequence);
      }
      break;
    case MsgType.Error:
      writers.push(writeErrorCode);
      break;
    default:
      throw new Error(`unsupported message type: ${msg.type}`);
  }

  writers.push(writePayload);
  return writers;
}

function getReaders(msg) {
  const readers = [];

  switch (msg.type) {
    case MsgType.AudioOnlyClient:
    case MsgType.AudioOnlyServer:
    case MsgType.FrontEndResultServer:
    case MsgType.FullClientRequest:
    case MsgType.FullServerResponse:
      if (msg.flag === MsgTypeFlagBits.PositiveSeq || msg.flag === MsgTypeFlagBits.NegativeSeq) {
        readers.push(readSequence);
      }
      break;
    case MsgType.Error:
      readers.push(readErrorCode);
      break;
    default:
      throw new Error(`unsupported message type: ${msg.type}`);
  }

  if (msg.flag === MsgTypeFlagBits.WithEvent) {
    readers.push(readEvent, readSessionId, readConnectId);
  }

  readers.push(readPayload);
  return readers;
}

function writeEvent(msg) {
  if (msg.event === undefined) return null;
  const buffer = new ArrayBuffer(4);
  const view = new DataView(buffer);
  view.setInt32(0, msg.event, false);
  return new Uint8Array(buffer);
}

function writeSessionId(msg) {
  if (msg.event === undefined) return null;

  switch (msg.event) {
    case EventType.StartConnection:
    case EventType.FinishConnection:
    case EventType.ConnectionStarted:
    case EventType.ConnectionFailed:
      return null;
  }

  const sessionId = msg.sessionId || '';
  const sessionIdBytes = Buffer.from(sessionId, 'utf8');
  const sizeBuffer = new ArrayBuffer(4);
  const sizeView = new DataView(sizeBuffer);
  sizeView.setUint32(0, sessionIdBytes.length, false);

  const result = new Uint8Array(4 + sessionIdBytes.length);
  result.set(new Uint8Array(sizeBuffer), 0);
  result.set(sessionIdBytes, 4);

  return result;
}

function writeSequence(msg) {
  if (msg.sequence === undefined) return null;
  const buffer = new ArrayBuffer(4);
  const view = new DataView(buffer);
  view.setInt32(0, msg.sequence, false);
  return new Uint8Array(buffer);
}

function writeErrorCode(msg) {
  if (msg.errorCode === undefined) return null;
  const buffer = new ArrayBuffer(4);
  const view = new DataView(buffer);
  view.setUint32(0, msg.errorCode, false);
  return new Uint8Array(buffer);
}

function writePayload(msg) {
  const payloadSize = msg.payload.length;
  const sizeBuffer = new ArrayBuffer(4);
  const sizeView = new DataView(sizeBuffer);
  sizeView.setUint32(0, payloadSize, false);

  const result = new Uint8Array(4 + payloadSize);
  result.set(new Uint8Array(sizeBuffer), 0);
  result.set(msg.payload, 4);

  return result;
}

function readEvent(msg, data, offset) {
  if (offset + 4 > data.length) {
    throw new Error('insufficient data for event');
  }
  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  msg.event = view.getInt32(0, false);
  return offset + 4;
}

function readSessionId(msg, data, offset) {
  if (msg.event === undefined) return offset;

  switch (msg.event) {
    case EventType.StartConnection:
    case EventType.FinishConnection:
    case EventType.ConnectionStarted:
    case EventType.ConnectionFailed:
    case EventType.ConnectionFinished:
      return offset;
  }

  if (offset + 4 > data.length) {
    throw new Error('insufficient data for session ID size');
  }

  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  const size = view.getUint32(0, false);
  offset += 4;

  if (size > 0) {
    if (offset + size > data.length) {
      throw new Error('insufficient data for session ID');
    }
    msg.sessionId = new TextDecoder().decode(data.slice(offset, offset + size));
    offset += size;
  }

  return offset;
}

function readConnectId(msg, data, offset) {
  if (msg.event === undefined) return offset;

  switch (msg.event) {
    case EventType.ConnectionStarted:
    case EventType.ConnectionFailed:
    case EventType.ConnectionFinished:
      break;
    default:
      return offset;
  }

  if (offset + 4 > data.length) {
    throw new Error('insufficient data for connect ID size');
  }

  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  const size = view.getUint32(0, false);
  offset += 4;

  if (size > 0) {
    if (offset + size > data.length) {
      throw new Error('insufficient data for connect ID');
    }
    msg.connectId = new TextDecoder().decode(data.slice(offset, offset + size));
    offset += size;
  }

  return offset;
}

function readSequence(msg, data, offset) {
  if (offset + 4 > data.length) {
    throw new Error('insufficient data for sequence');
  }
  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  msg.sequence = view.getInt32(0, false);
  return offset + 4;
}

function readErrorCode(msg, data, offset) {
  if (offset + 4 > data.length) {
    throw new Error('insufficient data for error code');
  }
  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  msg.errorCode = view.getUint32(0, false);
  return offset + 4;
}

function readPayload(msg, data, offset) {
  if (offset + 4 > data.length) {
    throw new Error('insufficient data for payload size');
  }

  const view = new DataView(data.buffer, data.byteOffset + offset, 4);
  const size = view.getUint32(0, false);
  offset += 4;

  if (size > 0) {
    if (offset + size > data.length) {
      throw new Error('insufficient data for payload');
    }
    msg.payload = data.slice(offset, offset + size);
    offset += size;
  }

  return offset;
}

const messageQueues = new Map();
const messageCallbacks = new Map();

function setupMessageHandler(ws) {
  if (!messageQueues.has(ws)) {
    messageQueues.set(ws, []);
    messageCallbacks.set(ws, []);

    ws.on('message', (data) => {
      try {
        let uint8Data;
        if (Buffer.isBuffer(data)) {
          uint8Data = new Uint8Array(data);
        } else if (data instanceof ArrayBuffer) {
          uint8Data = new Uint8Array(data);
        } else if (data instanceof Uint8Array) {
          uint8Data = data;
        } else {
          throw new Error(`Unexpected WebSocket message type: ${typeof data}`);
        }

        const msg = unmarshalMessage(uint8Data);
        const queue = messageQueues.get(ws);
        const callbacks = messageCallbacks.get(ws);

        if (callbacks.length > 0) {
          const callback = callbacks.shift();
          callback(msg);
        } else {
          queue.push(msg);
        }
      } catch (error) {
        throw new Error(`Error processing message: ${error}`);
      }
    });

    ws.on('close', () => {
      messageQueues.delete(ws);
      messageCallbacks.delete(ws);
    });
  }
}

async function receiveMessage(ws) {
  setupMessageHandler(ws);

  return new Promise((resolve, reject) => {
    const queue = messageQueues.get(ws);
    const callbacks = messageCallbacks.get(ws);

    if (queue.length > 0) {
      resolve(queue.shift());
      return;
    }

    const errorHandler = (error) => {
      const index = callbacks.findIndex((cb) => cb === resolver);
      if (index !== -1) {
        callbacks.splice(index, 1);
      }
      reject(error);
    };

    const resolver = (msg) => {
      ws.removeListener('error', errorHandler);
      resolve(msg);
    };

    callbacks.push(resolver);
    ws.once('error', errorHandler);
  });
}

async function waitForEvent(ws, msgType, eventType) {
  const msg = await receiveMessage(ws);
  if (msg.type !== msgType || msg.event !== eventType) {
    throw new Error(
      `Unexpected message: type=${getMsgTypeName(msg.type)}, event=${getEventTypeName(msg.event || 0)}`,
    );
  }
  return msg;
}

async function startConnection(ws) {
  const msg = createMessage(MsgType.FullClientRequest, MsgTypeFlagBits.WithEvent);
  msg.event = EventType.StartConnection;
  msg.payload = new TextEncoder().encode('{}');
  console.log(`${msg.toString()}`);
  const data = marshalMessage(msg);
  return new Promise((resolve, reject) => {
    ws.send(data, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function finishConnection(ws) {
  const msg = createMessage(MsgType.FullClientRequest, MsgTypeFlagBits.WithEvent);
  msg.event = EventType.FinishConnection;
  msg.payload = new TextEncoder().encode('{}');
  console.log(`${msg.toString()}`);
  const data = marshalMessage(msg);
  return new Promise((resolve, reject) => {
    ws.send(data, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function startSession(ws, payload, sessionId) {
  const msg = createMessage(MsgType.FullClientRequest, MsgTypeFlagBits.WithEvent);
  msg.event = EventType.StartSession;
  msg.sessionId = sessionId;
  msg.payload = payload;
  console.log(`${msg.toString()}`);
  const data = marshalMessage(msg);
  return new Promise((resolve, reject) => {
    ws.send(data, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function finishSession(ws, sessionId) {
  const msg = createMessage(MsgType.FullClientRequest, MsgTypeFlagBits.WithEvent);
  msg.event = EventType.FinishSession;
  msg.sessionId = sessionId;
  msg.payload = new TextEncoder().encode('{}');
  console.log(`${msg.toString()}`);
  const data = marshalMessage(msg);
  return new Promise((resolve, reject) => {
    ws.send(data, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function taskRequest(ws, payload, sessionId) {
  const msg = createMessage(MsgType.FullClientRequest, MsgTypeFlagBits.WithEvent);
  msg.event = EventType.TaskRequest;
  msg.sessionId = sessionId;
  msg.payload = payload;
  console.log(`${msg.toString()}`);
  const data = marshalMessage(msg);
  return new Promise((resolve, reject) => {
    ws.send(data, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

// ===== Main Test =====

const APPID = '6961020688';
const ACCESS_TOKEN = 'CIdpRRljwhn4xJMqlqp_5neZ_Z7Z11x3';
const VOICE_TYPE = 'BV001_streaming';
const ENDPOINT = 'wss://openspeech.bytedance.com/api/v3/tts/bidirection';

function voiceToResourceId(voice) {
  if (voice.startsWith('S_')) {
    return 'volc.megatts.default';
  }
  return 'volc.service_type.10029';
}

async function main() {
  const text = process.argv[2] || '你好，这是一个火山引擎语音合成的测试。';
  console.log('Testing Volcano Engine TTS v3 WebSocket API');
  console.log('Text:', text);
  console.log('Voice:', VOICE_TYPE);
  console.log('---');

  const headers = {
    'X-Api-App-Key': APPID,
    'X-Api-Access-Key': ACCESS_TOKEN,
    'X-Api-Resource-Id': voiceToResourceId(VOICE_TYPE),
    'X-Api-Connect-Id': uuidv4(),
  };

  console.log('Connecting to', ENDPOINT);
  console.log('Headers:', JSON.stringify(headers, null, 2));

  const ws = new WebSocket(ENDPOINT, {
    headers,
    skipUTF8Validation: true,
  });

  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', (err) => {
      console.error('WebSocket error:', err.message);
      reject(err);
    });
  });

  console.log('WebSocket connected!');

  await startConnection(ws);

  console.log(
    `${await waitForEvent(ws, MsgType.FullServerResponse, EventType.ConnectionStarted).then((msg) => msg.toString())}`,
  );

  const requestTemplate = {
    user: {
      uid: uuidv4(),
    },
    req_params: {
      speaker: VOICE_TYPE,
      audio_params: {
        format: 'mp3',
        sample_rate: 24000,
        enable_timestamp: true,
      },
      additions: JSON.stringify({
        disable_markdown_filter: false,
      }),
    },
  };

  const sentences = text
    .split('。')
    .filter((s) => s.trim().length > 0);

  let audioReceived = false;

  for (let i = 0; i < sentences.length; i++) {
    const sessionId = uuidv4();

    await startSession(
      ws,
      new TextEncoder().encode(
        JSON.stringify({
          ...requestTemplate,
          event: EventType.StartSession,
        }),
      ),
      sessionId,
    );

    console.log(
      `${await waitForEvent(ws, MsgType.FullServerResponse, EventType.SessionStarted).then((msg) => msg.toString())}`,
    );

    for (const char of sentences[i]) {
      await taskRequest(
        ws,
        new TextEncoder().encode(
          JSON.stringify({
            ...requestTemplate,
            req_params: {
              ...requestTemplate.req_params,
              text: char,
            },
            event: EventType.TaskRequest,
          }),
        ),
        sessionId,
      );
    }

    await finishSession(ws, sessionId);

    const audio = [];
    while (true) {
      const msg = await receiveMessage(ws);
      console.log(`${msg.toString()}`);

      switch (msg.type) {
        case MsgType.FullServerResponse:
          break;
        case MsgType.AudioOnlyServer:
          if (!audioReceived && audio.length > 0) {
            audioReceived = true;
          }
          audio.push(msg.payload);
          break;
        default:
          throw new Error(`${msg.toString()}`);
      }
      if (msg.event === EventType.SessionFinished) {
        break;
      }
    }

    if (audio.length > 0) {
      const outputFile = `${VOICE_TYPE}_session_${i}.mp3`;
      const totalLength = audio.reduce((sum, buf) => sum + buf.length, 0);
      const result = new Uint8Array(totalLength);
      let offset = 0;
      for (const buf of audio) {
        result.set(buf, offset);
        offset += buf.length;
      }
      await fs.promises.writeFile(outputFile, result);
      console.log(`Audio saved to ${outputFile}`);
    }
  }

  await finishConnection(ws);

  console.log(
    `${await waitForEvent(ws, MsgType.FullServerResponse, EventType.ConnectionFinished).then((msg) => msg.toString())}`,
  );

  if (!audioReceived) {
    throw new Error('no audio received');
  }

  ws.close();
  console.log('Test completed successfully!');
}

main().catch((err) => {
  console.error('Test failed:', err);
  process.exit(1);
});

// Jest setup (picked up automatically by react-scripts).
// jsdom in Jest 27 has no TextEncoder/TextDecoder, which react-router v7 needs
// at import time.
import { TextDecoder, TextEncoder } from 'util';

if (typeof globalThis.TextEncoder === 'undefined') globalThis.TextEncoder = TextEncoder;
if (typeof globalThis.TextDecoder === 'undefined') globalThis.TextDecoder = TextDecoder;

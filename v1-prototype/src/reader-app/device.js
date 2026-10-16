// What to call the device Flipparr View is running on, in what it says
// ("kept on this iPhone"). It was "iPad" everywhere until 2026-10-16, which
// read wrong on a phone. An iPad's web view reports itself as a Mac, so
// anything that is not an iPhone is an iPad here.
export const DEVICE = typeof navigator !== "undefined" && /iPhone|iPod/.test(navigator.userAgent || "") ? "iPhone" : "iPad";

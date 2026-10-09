/**
 * Small event bridges between the fetch layer (which has no React) and the providers (which have no fetch layer):
 * API activity for the idle warning, "the session ended" for the 401 rule, and the step-up request.
 */

import type { Method } from "@agentledger/contracts";

type Listener<T> = (value: T) => void;

function emitter<T>() {
  const listeners = new Set<Listener<T>>();
  return {
    emit(value: T): void {
      for (const l of listeners) l(value);
    },
    subscribe(l: Listener<T>): () => void {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
  };
}

/** When the API last answered (any status); the idle warning counts from here. */
let lastActivity = Date.now();
const activityEmitter = emitter<number>();

export const activity = {
  touch(at: number = Date.now()): void {
    lastActivity = at;
    activityEmitter.emit(at);
  },
  last(): number {
    return lastActivity;
  },
  subscribe(listener: Listener<number>): () => void {
    return activityEmitter.subscribe(listener);
  },
};

/** A 401 outside /api/auth/*: the session ended at the API. */
export const sessionEnded = emitter<{ path: string }>();

export interface StepUpRequest {
  path: string;
  method: Method;
}

type StepUpHandler = (request: StepUpRequest) => Promise<boolean>;

let stepUpHandler: StepUpHandler | null = null;

/** The fetch layer asks; the StepUp provider answers (true: verified, retry the request). */
export const stepUpBridge = {
  request(request: StepUpRequest): Promise<boolean> {
    return stepUpHandler ? stepUpHandler(request) : Promise.resolve(false);
  },
  setHandler(handler: StepUpHandler): () => void {
    stepUpHandler = handler;
    return () => {
      if (stepUpHandler === handler) stepUpHandler = null;
    };
  },
};

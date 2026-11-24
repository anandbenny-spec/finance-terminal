import { parentPort } from "node:worker_threads";

let timer;
parentPort.on("message", (deadline) => {
  clearTimeout(timer);
  if (deadline !== null) {
    timer = setTimeout(() => process.kill(process.pid, "SIGKILL"), Math.max(0, deadline - performance.now()));
  }
});

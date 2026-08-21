/**
 * 약국 조제 화면 (React 판).
 *
 * 원본은 `omx/web/templates/index.html` (Flask + vanilla JS). 백엔드가
 * FastAPI 로 이관되면서 화면도 프론트로 옮겨왔다.
 *
 * ## 설계상 유지된 계약
 *
 *  - **화면이 보이는 조합이 언제나 진실이다.** 무작위로 다시 뽑으면 서버의
 *    원본과 화면 상태가 갈리므로, 조제 요청은 조합을 함께 실어 보낸다.
 *  - **RANDOM 후 reset() 은 원본을 다시 받아 온다.** 그렇지 않으면
 *    "처음부터" 를 눌러도 랜덤 조합이 그대로 남는다.
 *  - **SSE 로 진행 상황을 받는다.** React 는 텍스트 노드를 자동 이스케이프하므로
 *    Flask 판에서 손으로 짜야 했던 `esc()` 는 필요 없다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getPolicies,
  getPrescriptions,
  getRandomPrescriptions,
  getTray,
  progressUrl,
  resetPharmacy,
  searchPatients,
  startDispense,
  startPack,
  stopDispense,
  type Color,
  type Patient,
  type Policy,
  type PrescriptionsResponse,
  type ProgressEvent,
  type TrayReading,
} from "../lib/pharmacyApi";
import "./PharmacyDashboard.css";

const KOR: Record<Color, string> = {
  red: "빨강",
  yellow: "노랑",
  green: "초록",
};
const COLORS: Color[] = ["red", "yellow", "green"];

// ── 아이콘 (Feather 계열, 인라인 SVG) ──────────────────────
// 아이콘 라이브러리를 새로 걸지 않는다 — 6종만 쓰고 다른 화면과 공유하지 않으므로
// 이 파일 안에서 완결하는 편이 의존성을 줄이고 트리셰이킹 계산도 없앤다.
type IconName = "arrow-left" | "play" | "stop" | "package" | "rotate-ccw";

function Icon({ name }: { name: IconName }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {name === "arrow-left" && (
        <>
          <line x1="19" y1="12" x2="5" y2="12" />
          <polyline points="12 19 5 12 12 5" />
        </>
      )}
      {name === "play" && <polygon points="6 3 20 12 6 21 6 3" />}
      {name === "stop" && <rect x="6" y="6" width="12" height="12" rx="1" />}
      {name === "package" && (
        <>
          <line x1="16.5" y1="9.4" x2="7.5" y2="4.21" />
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
          <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
          <line x1="12" y1="22.08" x2="12" y2="12" />
        </>
      )}
      {name === "rotate-ccw" && (
        <>
          <polyline points="1 4 1 10 7 10" />
          <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
        </>
      )}
    </svg>
  );
}

// 라디오 표시. 실제 form input 이 아니라 순수 시각 표기 —
// 카드 전체(`<button>`)가 클릭 대상이라 별도의 input 을 두면 접근성 트리에 중복이 생긴다.
function Radio({ on }: { on: boolean }) {
  return (
    <span className={`pharm-radio ${on ? "on" : ""}`} aria-hidden="true" />
  );
}

// 진행 단계의 상태. 원본 JS 의 stepState 와 같은 축이다.
type StepState = "" | "now" | "done" | "fail";
// 조제 전체 상태 (버튼 활성/문구용).
type JobState = "idle" | "running" | "done" | "bad";
type LogKind = "" | "ok" | "warn" | "bad";
interface LogItem {
  at: string;
  msg: string;
  kind: LogKind;
}

export function PharmacyDashboard() {
  // 서버에서 받은 원본. 무작위를 누르면 이 배열이 바뀌었다가 reset 으로 되돌아온다.
  const [rx, setRx] = useState<PrescriptionsResponse | null>(null);
  const [pol, setPol] = useState<Policy[]>([]);
  const [polSel, setPolSel] = useState<string>("");
  const [rxSel, setRxSel] = useState<string | null>(null);
  const [pt, setPt] = useState<Patient | null>(null);
  const [tray, setTray] = useState<TrayReading | null>(null);
  const [trayBusy, setTrayBusy] = useState(false);
  const [stepState, setStepState] = useState<StepState[]>([]);
  // 포장 중 로봇이 도는 단계의 진행률. null 이면 막대를 그리지 않는다.
  const [packProg, setPackProg] = useState<{ 이름: string; 값: number } | null>(
    null,
  );
  // 수령 안내 — 모든 작업이 끝났을 때 뜨는 배너. 시연에서 제일 크게 보여야 하는
  // 화면이라 진행·알림 카드보다 위, 화면 전체 폭으로 둔다. null 이면 안 뜬다.
  const [pickup, setPickup] = useState<{
    환자: string;
    병명: string;
    처방: string;
    시각: string;
  } | null>(null);
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [job, setJob] = useState<JobState>("idle");
  const [stateText, setStateText] = useState("대기");

  // 로그·상태 등 최신 값을 SSE 콜백에서 읽으려면 ref 로 붙잡아 둔다.
  // 그렇게 하지 않으면 EventSource 콜백이 초기 클로저의 값을 계속 쓴다.
  const rxRef = useRef<PrescriptionsResponse | null>(null);
  rxRef.current = rx;
  const rxSelRef = useRef<string | null>(null);
  rxSelRef.current = rxSel;
  const ptRef = useRef<Patient | null>(null);
  ptRef.current = pt;

  const log = useCallback((msg: string, kind: LogKind = "") => {
    const d = new Date();
    const at =
      `${String(d.getHours()).padStart(2, "0")}:` +
      `${String(d.getMinutes()).padStart(2, "0")}:` +
      `${String(d.getSeconds()).padStart(2, "0")}`;
    setLogs((prev) => [{ at, msg, kind }, ...prev].slice(0, 200));
  }, []);

  const setState = useCallback((text: string, jobState: JobState) => {
    setStateText(text);
    setJob(jobState);
  }, []);

  // ── 초기 로드 ──────────────────────────────────────────
  // 처방·정책·트레이를 모두 병렬로 시작한다. 예전엔 트레이가 마지막에 await 되었는데,
  // 그 사이 UI 는 이미 "시뮬레이션 모드로 읽었습니다" (기본값) 를 보여 실제로 아무것도
  // 읽지 않은 시각에 잘못된 상태를 표시했다. 트레이도 여기서 나란히 시작해 화면과
  // 실제 상태가 항상 일치하도록 한다.
  useEffect(() => {
    let disposed = false;
    Promise.all([getPrescriptions(), getPolicies()])
      .then(([r, p]) => {
        if (disposed) return;
        setRx(r);
        setPol(p.정책);
        // 초기 선택 없이 시작해 사용자가 명시적으로 정책을 고르게 한다.
        // 서버 기본값(p.기본)을 자동 선택하면 화면이 라디오만 미리 채워 놓아
        // "무엇을 골랐는지" 를 사용자가 의식하지 못한 채 조제가 나간다.
      })
      .catch((e) => log(`처방/정책 로드 실패: ${String(e)}`, "bad"));
    refreshTray();
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 트레이 ─────────────────────────────────────────────
  // 실제 모드에서는 조제 파트의 top 카메라를 열어 여러 장을 찍으므로 몇 초 걸린다.
  // 그동안 버튼을 잠근다 — 카메라는 두 번 열리지 않아 서버가 잠금으로 직렬화하므로,
  // 연타해 봐야 줄만 서고 화면은 응답이 없는 것처럼 보인다.
  const refreshTray = useCallback(async () => {
    setTrayBusy(true);
    try {
      const t = await getTray();
      setTray(t);
      // 카드에도 뜨지만 로그에 남겨야 언제 실패했는지가 남는다.
      if (t.오류) log(`트레이 확인 실패 — ${t.오류}`, "bad");
    } catch (e) {
      log(`트레이 확인 실패: ${String(e)}`, "bad");
    } finally {
      setTrayBusy(false);
    }
  }, [log]);

  // ── SSE 구독 ──────────────────────────────────────────
  // 마운트 후 한 번만 붙는다. React StrictMode 는 dev 에서 effect 를 두 번
  // 부르지만, cleanup 이 EventSource 를 닫고 서버가 fan-out 이라 중복 이벤트도
  // 없다.
  useEffect(() => {
    let es: EventSource | null = null;
    let 마지막수신 = Date.now();
    let 정리됨 = false;

    const onMessage = (ev: MessageEvent) => {
      마지막수신 = Date.now();
      let e: ProgressEvent;
      try {
        e = JSON.parse(ev.data) as ProgressEvent;
      } catch {
        return;
      }
      const rxNow = rxRef.current;
      const pNow = rxNow?.처방.find((x) => x.코드 === rxSelRef.current) ?? null;

      switch (e.종류) {
        case "단계시작":
          setStepState((s) => {
            const c = [...s];
            c[e.순번 - 1] = "now";
            return c;
          });
          log(`${e.순번}번째 — ${e.색이름} ${e.약} 집는 중`);
          break;
        case "알림":
          log(e.글, (e.급 as LogKind) || "");
          break;
        case "단계끝":
          setStepState((s) => {
            const c = [...s];
            c[e.순번 - 1] = e.성공 ? "done" : "fail";
            return c;
          });
          log(
            e.성공
              ? `${e.순번}번째 완료 — ${e.메모}`
              : `${e.순번}번째 실패 — ${e.메모}`,
            e.성공 ? "ok" : "bad",
          );
          break;
        case "조제완료":
          setState("조제 완료 · 포장 대기", "done");
          log("조제가 끝났습니다. 포장하기를 누르세요.", "ok");
          refreshTray();
          break;
        case "포장시작":
          if (pNow) {
            setStepState((s) => {
              const c = [...s];
              c[pNow.조합.length] = "now";
              return c;
            });
          }
          setState("포장 중", "running");
          setPackProg(null);
          setPickup(null);
          log("포장을 시작합니다");
          break;
        case "포장단계":
          // 다음 단계로 넘어갔으면 앞 단계의 막대는 지운다. 시뮬레이션 단계는
          // 진행률이 없으므로 막대 없이 로그만 남는다.
          setPackProg(null);
          log(`포장 — ${e.이름}`);
          break;
        case "포장진행":
          setPackProg({ 이름: e.이름, 값: e.진행 });
          break;
        case "완료":
          if (pNow) {
            setStepState((s) => {
              const c = [...s];
              c[pNow.조합.length] = "done";
              return c;
            });
          }
          setState("완료", "done");
          setPackProg(null);
          setPickup({
            환자: ptRef.current?.이름 ?? "",
            병명: ptRef.current?.병명 ?? "",
            처방: pNow?.설명 ?? "",
            시각: e.시각,
          });
          log(`모든 작업 완료 (${e.시각})`, "ok");
          break;
        case "중단":
          setState("중단됨", "bad");
          setPackProg(null);
          log("중단: " + e.이유, "bad");
          break;
      }
    };
    const 붙이기 = () => {
      es = new EventSource(progressUrl());
      마지막수신 = Date.now();
      es.onmessage = onMessage;
      es.onerror = () => log("서버 연결이 끊겼습니다", "warn");
    };

    // **조용히 죽은 스트림 워치독.** 백엔드를 재시작하면 vite 프록시가 클라이언트
    // 소켓을 붙잡은 채 남아, 브라우저는 스트림이 열린 줄 안다 — onerror 도
    // EventSource 자동 재연결도 오지 않고 화면만 영영 멈춘다. 새로고침 전까지
    // 조제 단계도 포장 진행률도 올라오지 않는다.
    //
    // 서버가 20초마다 `핑` 을 보내므로, 45초(핑 두 번을 놓친 것) 넘게 아무것도
    // 없으면 직접 다시 붙는다. 새로고침과 달리 고른 환자·처방은 그대로 남는다.
    const 감시 = setInterval(() => {
      if (정리됨 || Date.now() - 마지막수신 < 45_000) return;
      log("진행 상황 연결이 조용히 끊겼습니다 — 다시 붙습니다", "warn");
      es?.close();
      붙이기();
    }, 5_000);

    붙이기();
    return () => {
      정리됨 = true;
      clearInterval(감시);
      es?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 환자 검색 ─────────────────────────────────────────
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Patient[] | null>(null);
  useEffect(() => {
    if (pt) {
      setHits(null);
      return;
    }
    let disposed = false;
    // 매 keystroke 마다 200ms 디바운스. 원본은 디바운스가 없었지만, 서버는
    // 어차피 인메모리라 부하 문제는 아니고 UX 안정성 목적이다.
    const timer = setTimeout(async () => {
      try {
        const list = await searchPatients(q);
        if (!disposed) setHits(list);
      } catch (e) {
        if (!disposed) log(`검색 실패: ${String(e)}`, "bad");
      }
    }, 200);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [q, pt, log]);

  const pickPatient = (p: Patient) => {
    setPt(p);
    setQ("");
    setHits(null);
    if (p.처방) {
      setRxSel(p.처방.코드);
      log(`${p.이름} 님 — ${p.병명}, 처방 ${p.처방.코드} 를 선택했습니다`);
    }
  };
  const clearPatient = () => {
    setPt(null);
    setQ("");
  };

  // ── 무작위 처방 ────────────────────────────────────────
  const [randBusy, setRandBusy] = useState(false);
  const [flash, setFlash] = useState(0);
  const drawRandom = async () => {
    setRandBusy(true);
    try {
      const d = await getRandomPrescriptions();
      setRx((cur) => (cur ? { ...cur, 처방: d.처방 } : cur));
      setFlash((n) => n + 1);
      log(
        "모든 처방의 색 조합을 새로 뽑았습니다 — 같은 병명도 순서가 달라집니다",
      );
    } catch (e: unknown) {
      const err = e as { response?: { data?: { 오류?: string } } };
      log(err.response?.data?.오류 ?? String(e), "bad");
    } finally {
      setRandBusy(false);
    }
  };

  // ── 조제 시작·중단·포장·리셋 ──────────────────────────
  const start = async () => {
    // 버튼이 startBlockedReason 을 보고 disabled 되므로 여기 도달하면 전제조건은 만족.
    // 여전히 방어적으로 종료 — 상태가 도중에 바뀐 경우 대비.
    if (!trayReady || !pt || !rxSel || !polSel) return;
    const r = rx?.처방.find((p) => p.코드 === rxSel);
    if (!r) return;
    setStepState([]);
    setPickup(null);
    try {
      const d = await startDispense({
        환자: {
          이름: pt.이름,
          id: pt.id,
          생년: pt.생년,
          성별: pt.성별,
          병명: pt.병명,
        },
        처방코드: rxSel,
        정책: polSel,
        조합: r.조합, // 화면의 조합이 진실이다
      });
      setState("조제 중", "running");
      log(`조제 시작 — ${d.처방.병명} (${d.정책})`);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { 오류?: string } } };
      log("⚠ " + (err.response?.data?.오류 ?? String(e)), "bad");
    }
  };

  const stop = async () => {
    try {
      await stopDispense();
      log("중단을 요청했습니다", "warn");
    } catch (e) {
      log(`중단 실패: ${String(e)}`, "bad");
    }
  };

  const pack = async () => {
    try {
      await startPack();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { 오류?: string } } };
      log("⚠ " + (err.response?.data?.오류 ?? String(e)), "bad");
    }
  };

  const reset = async () => {
    try {
      await resetPharmacy();
      // 무작위 처방을 눌렀다면 서버 원본을 다시 받아 온다.
      const fresh = await getPrescriptions();
      setRx(fresh);
      clearPatient();
      setRxSel(null);
      setStepState([]);
      setPackProg(null);
      setPickup(null);
      setLogs([]);
      setState("대기", "idle");
      await refreshTray();
      log("초기화했습니다 — 환자를 검색해 다시 시작하세요");
    } catch (e: unknown) {
      const err = e as { response?: { data?: { 오류?: string } } };
      log(err.response?.data?.오류 ?? String(e), "bad");
    }
  };

  // ── 파생값 ────────────────────────────────────────────
  const selectedRx = useMemo(
    () => rx?.처방.find((p) => p.코드 === rxSel) ?? null,
    [rx, rxSel],
  );
  // 아직 안 읽었거나 카메라가 실패한 상태를 0 으로 그리면 "트레이가 비었다" 로
  // 읽힌다. 모르는 것은 모른다고(—) 표시한다.
  const trayCounts = tray?.개수 ?? null;
  const packDone = job === "done" && stateText.startsWith("완료");

  // 트레이를 실제로 읽어 개수를 받은 상태만 "연결됨" 이다. 시뮬레이션도 연결로
  // 친다 — 조제 파트 없이 도는 것이 기본 모드고, 그때도 개수는 서버가 준다.
  const trayReady = tray !== null && !tray.오류 && trayCounts !== null;

  // 선택한 처방이 요구하는 색 중 트레이에 없는 것. 로봇은 없는 색을 찾다가
  // 제한 시간을 다 쓰므로, 시작하기 전에 화면에서 막는다.
  const trayShort = useMemo(() => {
    if (!selectedRx || trayCounts === null) return [];
    return [...new Set(selectedRx.조합)].filter((c) => (trayCounts[c] ?? 0) < 1);
  }, [selectedRx, trayCounts]);

  // "조제 시작" 이 아직 못 눌리는 이유. null 이면 준비 완료.
  // 조제 방식 카드 하단 CTA 옆에 힌트로 붙는다.
  //
  // **트레이 연결 확인이 첫 관문이다.** 환자·처방·정책을 다 고른 뒤에 카메라가
  // 죽어 있는 것을 알게 되면 고른 것을 다 버리게 되고, 실제 모드에서는 로봇이
  // 빈 트레이를 뒤지다 제한 시간을 태운다.
  const startBlockedReason: string | null = trayBusy
    ? "트레이를 확인하는 중입니다"
    : tray === null
      ? "트레이 연결을 먼저 확인하세요"
      : tray.오류
        ? "트레이가 연결되지 않았습니다 — 다시 확인하세요"
        : !pt
          ? "환자를 선택하세요"
          : !rxSel
            ? "처방을 선택하세요"
            : trayShort.length > 0
              ? `트레이에 ${trayShort.map((c) => KOR[c]).join("·")} 알약이 없습니다`
              : !polSel
                ? "조제 방식을 선택하세요"
                : job === "running"
                  ? "조제가 진행 중입니다"
                  : null;

  const stepItems = useMemo(() => {
    if (!selectedRx || !rx) return [];
    return [
      ...selectedRx.조합.map((c, i) => ({
        label: `${KOR[c]} · ${rx.약품[c].이름}`,
        state: stepState[i] ?? ("" as StepState),
      })),
      {
        label: "포장",
        state: (stepState[selectedRx.조합.length] ?? "") as StepState,
      },
    ];
  }, [selectedRx, rx, stepState]);

  // ── 렌더 ─────────────────────────────────────────────
  return (
    <div className="pharm">
      <div className="pharm-container">
        <div className="pharm-grid pharm-g2 pharm-g2--fixed">
          {/* 환자 */}
          <section className="pharm-card">
            <h2>환자 정보</h2>
            {!pt ? (
              <div className="pharm-picker">
                <div className="pharm-search">
                  <input
                    placeholder="이름 또는 생년월일로 검색  (예: 김수진, 1978-03-14)"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    autoComplete="off"
                  />
                </div>
                <p className="pharm-hint">
                  환자를 눌러 선택하면 병명과 처방이 자동으로 채워집니다.
                </p>
                <div className="pharm-hits">
                  {hits && hits.length === 0 ? (
                    <div className="pharm-empty">일치하는 환자가 없습니다</div>
                  ) : (
                    (hits ?? []).map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        className="pharm-hit"
                        onClick={() => pickPatient(p)}
                      >
                        <div>
                          <b>{p.이름}</b>{" "}
                          <span className="m">
                            {p.id} · {p.성별} · {p.생년}
                          </span>
                        </div>
                        <span className="dxc">{p.병명}</span>
                      </button>
                    ))
                  )}
                </div>
              </div>
            ) : (
              <div className="pharm-info">
                <div className="pharm-ptop">
                  <div className="pharm-name">{pt.이름}</div>
                  <button className="pharm-btn sm" onClick={clearPatient}>
                    <Icon name="arrow-left" />
                    다른 환자
                  </button>
                </div>
                <div className="pharm-dx">
                  <span className="pharm-tag">{pt.병명}</span>
                </div>
                <dl>
                  <dt>환자 ID</dt>
                  <dd className="mono">{pt.id}</dd>
                  <dt>생년월일</dt>
                  <dd className="mono">{pt.생년}</dd>
                  <dt>성별</dt>
                  <dd>{pt.성별}</dd>
                  <dt>담당의</dt>
                  <dd>{pt.담당의}</dd>
                </dl>
              </div>
            )}
          </section>

          {/* 트레이 — 조제의 첫 관문이라 연결 상태를 제목 옆에 붙인다. */}
          <section className="pharm-card">
            <div className="pharm-card__head">
              <h2>트레이 알약</h2>
              <span
                className={`pharm-badge ${
                  trayBusy ? "exp" : trayReady ? "rec" : tray ? "bad" : "exp"
                }`}
              >
                {trayBusy
                  ? "확인 중"
                  : trayReady
                    ? "연결됨"
                    : tray
                      ? "연결 실패"
                      : "확인 필요"}
              </span>
            </div>
            <div className="pharm-tray">
              {COLORS.map((c) => {
                const v = trayCounts?.[c] ?? null;
                return (
                  <div key={c} className="pharm-tc">
                    <div
                      className="dot"
                      style={{
                        background: `linear-gradient(to bottom, var(--pill-${c}) 0 50%, #fff 50% 100%)`,
                      }}
                    />
                    <div className="n">
                      <span>{v === null ? "—" : v}</span>
                      {v !== null && <span className="unit">알</span>}
                    </div>
                    <div className="l">
                      {KOR[c]} · {rx?.약품[c]?.이름 ?? ""}
                    </div>
                  </div>
                );
              })}
            </div>
            <div
              className="pharm-actions pharm-actions--stack"
              style={{ marginTop: "24px", paddingTop: 12 }}
            >
              <span className="pharm-tray__status">
                {trayBusy
                  ? "카메라로 읽는 중…"
                  : tray === null
                    ? "아직 확인하지 않았습니다"
                    : tray.오류
                      ? `⚠ ${tray.오류}`
                      : `${tray.모드} 모드 · ${tray.시각} 에 읽었습니다`}
              </span>
              {/* 진행 표시는 위 상태 줄이 맡는다 ("카메라로 읽는 중…").
                  버튼까지 "읽는 중…" 으로 바꾸면 같은 말을 두 번 하는 셈이고,
                  누르고 나서 무엇을 하는 버튼이었는지가 사라진다. */}
              <button
                className="pharm-btn"
                onClick={refreshTray}
                disabled={trayBusy}
              >
                <Icon name="rotate-ccw" />
                {tray === null ? "트레이 확인" : "다시 확인"}
              </button>
            </div>
          </section>
        </div>

        {/* 처방 */}
        <section className="pharm-card" style={{ marginTop: 18 }}>
          <div className="pharm-card__head">
            <h2>처방 선택</h2>
            {/* 무작위 조합은 "트레이에 있는 색" 에서만 뽑는다. 트레이를 못 읽은
                상태에서는 서버가 503 을 돌려주므로 아예 못 누르게 한다. */}
            <button
              className="pharm-btn sm"
              onClick={drawRandom}
              disabled={randBusy || !trayReady}
              title={
                trayReady
                  ? "병명은 그대로 두고 모든 처방의 색 조합과 순서를 새로 뽑습니다"
                  : "트레이 연결을 먼저 확인하세요"
              }
            >
              <span aria-hidden="true">🎲</span>
              {randBusy ? "뽑는 중…" : "무작위로 다시 뽑기"}
            </button>
          </div>
          <div key={flash} className={`pharm-rx ${flash ? "pharm-flash" : ""}`}>
            {rx?.처방.map((p) => (
              <button
                key={p.코드}
                className={`pharm-rxit ${rxSel === p.코드 ? "on" : ""}`}
                onClick={() => setRxSel(p.코드)}
              >
                <Radio on={rxSel === p.코드} />
                <div className="pharm-pills">
                  {p.조합.map((c, i) => (
                    <span
                      key={i}
                      className={`pharm-pill ${c}`}
                      title={KOR[c]}
                    />
                  ))}
                </div>
                <div className="pharm-rxit-body">
                  <b>
                    {p.병명} <span className="code">({p.코드})</span>
                  </b>
                  <small>
                    {/* 초기/무작위 모두 색 순서로 표기. 병명 옆에 이미 진단명이 있어
                        약품명을 다시 쓰기보다는 담을 순서를 보여 준다. */}
                    {p.조합.map((c) => KOR[c]).join(" → ")}
                  </small>
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* 정책 */}
        <section className="pharm-card" style={{ marginTop: 18 }}>
          <h2>조제 방식</h2>
          <div className="pharm-pol">
            {pol.map((p) => (
              <button
                key={p.id}
                className={`pharm-polit ${polSel === p.id ? "on" : ""}`}
                onClick={() => setPolSel(p.id)}
              >
                <Radio on={polSel === p.id} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="t">{p.이름}</div>
                  <div className="s">{p.부제}</div>
                </div>
                <span className={`pharm-badge ${p.추천 ? "rec" : "exp"}`}>
                  {p.추천 ? "검증됨" : "실험중"}
                </span>
              </button>
            ))}
          </div>
          {/* "조제 방식" 을 마지막으로 선택하는 흐름이라 다음 액션인 "조제 시작" 을
              이 카드 하단에 두어 시선을 이어 준다. 진행 중 제어 (중단·포장·처음부터)
              는 아래 "진행 상황" 카드에 남긴다. */}
          <div className="pharm-cta">
            <button
              className="pharm-btn pri lg"
              onClick={start}
              disabled={startBlockedReason !== null}
            >
              <Icon name="play" />
              조제 시작
            </button>
            <span className="pharm-cta__hint">{startBlockedReason ?? ""}</span>
          </div>
        </section>

        {/* 수령 안내 — 시연의 마지막 장면. 진행·알림 카드 위에 화면 전체 폭으로
            둔다. 멀리서도 환자 이름이 읽혀야 해서 이름을 제일 크게 쓴다. */}
        {pickup && (
          <section className="pharm-pickup" role="status" aria-live="polite">
            <div className="mark">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </div>
            <div className="body">
              {/* **"잘 담겼습니다" 로 쓰지 않는다.** ACT 는 성공 신호를 내놓지
                  않아서 이 배너는 제한 시간이 지나면 무조건 뜬다 — 팔이 약통을
                  놓쳐도 똑같이 뜬다. 로봇이 확인할 수 없는 것을 로봇 입으로
                  보증하게 만들지 않는다. 성공 판정은 사람이 한다
                  (omx/il/TASK.md 의 성공 기준). */}
              <p className="head">조제 · 포장이 끝났습니다</p>
              <p className="who">
                <strong>{pickup.환자 || "환자"}</strong> 님, 약 찾아가세요
              </p>
              <p className="meta">
                {[pickup.병명, pickup.처방].filter(Boolean).join(" · ")}
                {pickup.시각 ? ` · ${pickup.시각}` : ""}
              </p>
            </div>
          </section>
        )}

        {/* 진행 · 알림 — 한 행에 나란히. 진행 상황이 주 시선, 알림은 로그 요약. */}
        <div
          className="pharm-grid pharm-g2 pharm-g2--tall"
          style={{ marginTop: 18, gridTemplateColumns: "3fr 2fr" }}
        >
          <section className="pharm-card pharm-card--flex">
            <h2>진행 상황</h2>
            <div className="pharm-steps">
              {stepItems.length === 0 ? (
                <div className="pharm-empty">
                  처방을 선택하면 단계가 표시됩니다
                </div>
              ) : (
                stepItems.map((it, i) => (
                  <div key={i} className={`pharm-st ${it.state}`}>
                    <div className={`bar ${it.state === "done" ? "done" : ""}`} />
                    <div className="c">
                      {it.state === "done"
                        ? "✓"
                        : it.state === "fail"
                          ? "!"
                          : i + 1}
                    </div>
                    <div className="lb">{it.label}</div>
                  </div>
                ))
              )}
            </div>
            {/* 로봇이 도는 단계의 진행 막대. 이것이 없으면 실제 모드에서 팔이
                움직이는 내내(기본 60초) 화면이 멈춘 것처럼 보인다.
                시간 기준이라 성공 여부가 아니다 — 그래서 % 를 크게 쓰지 않고
                단계 이름 옆에 작게만 붙인다. */}
            {packProg && (
              <div className="pharm-packprog">
                <div className="lb">
                  <span>{packProg.이름}</span>
                  <span className="pct">
                    {Math.round(packProg.값 * 100)}%
                  </span>
                </div>
                <div className="track">
                  <div
                    className="fill"
                    style={{ width: `${Math.round(packProg.값 * 100)}%` }}
                  />
                </div>
                <p className="pharm-hint">
                  진행률은 시간 기준입니다 — 성공 여부는 눈으로 확인하세요.
                </p>
              </div>
            )}
            <div
              className="pharm-actions"
              style={{ marginTop: "auto", paddingTop: 18 }}
            >
              <button
                className="pharm-btn"
                onClick={stop}
                disabled={job !== "running"}
              >
                <Icon name="stop" />
                중단
              </button>
              <button
                className="pharm-btn ok"
                onClick={pack}
                disabled={job !== "done" || packDone}
              >
                <Icon name="package" />
                포장하기
              </button>
              <button className="pharm-btn" onClick={reset}>
                <Icon name="rotate-ccw" />
                처음부터
              </button>
              <span className={`pharm-state ${job === "idle" ? "" : job}`}>
                <span className="d" />
                {stateText}
              </span>
            </div>
          </section>

          <section className="pharm-card pharm-card--flex">
            <h2>알림</h2>
            <div className="pharm-logs">
              {logs.length === 0 ? (
                <div className="pharm-empty">아직 기록이 없습니다</div>
              ) : (
                logs.map((l, i) => (
                  <div key={i} className={`pharm-log ${l.kind}`}>
                    <span className="msg">{l.msg}</span>
                    <span className="t">{l.at}</span>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

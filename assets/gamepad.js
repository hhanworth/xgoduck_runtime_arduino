// Xbox / standard Gamepad API input. No MCU protocol or Linux dependencies.
(function () {
  'use strict';
  const dead = v => {
    if (!Number.isFinite(v)) return 0;
    const a = Math.min(1, Math.abs(v));
    return a <= 0.12 ? 0 : Math.sign(v) * (a - 0.12) / 0.88;
  };
  const axis = (pad, index) => dead(pad.axes[index] || 0);
  const down = (pad, index) => Boolean(pad.buttons[index] && pad.buttons[index].pressed);
  const trigger = (pad, index) => Math.max(0, Math.min(1, Number(pad.buttons[index]?.value) || 0));
  // Standard mapping: A=0 B=1 X=2 Y=3 LB=4 RB=5 LT=6 RT=7
  // Back=8 Start=9 left/right stick=10/11, D-pad=12..15.
  function decode(pad) {
    if (!pad || !pad.connected || pad.mapping !== 'standard' ||
        pad.axes.length < 4 || pad.buttons.length < 16) return null;
    const shift = down(pad, 4);
    return {
      twist: shift ? [0, 0, 0] : [
        -axis(pad, 1) * 0.4,
        -axis(pad, 0) * 0.3,
        -axis(pad, 2)
      ],
      head: shift ? [axis(pad, 0), -axis(pad, 1), axis(pad, 2), -axis(pad, 3)] : [0, 0, 0, 0],
      mouth: Math.round(trigger(pad, 7) * 30),
      // Emergency stop is available even before taking control.
      stop: down(pad, 1),
      hold: down(pad, 0),
      walk: down(pad, 9),
      pick: down(pad, 2),
      roulade: down(pad, 3),
      neutral: Math.abs(axis(pad, 0)) + Math.abs(axis(pad, 1)) +
               Math.abs(axis(pad, 2)) + Math.abs(axis(pad, 3)) < 0.001
    };
  }
  window.XboxGamepad = { decode, dead };
})();

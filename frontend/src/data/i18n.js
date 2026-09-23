export const MESSAGES = {
  "nudge.seatbelt.off_while_operating": "Seatbelt off while operating — stop and fasten your belt.",
  "nudge.seatbelt.off_parked": "Seatbelt unfastened.",
  "nudge.exit_guard.before_exiting": "Before exiting: lower the bucket, lock hydraulics, stop the engine.",
  "nudge.exit.secured_engine_on": "Machine secured. Engine still running — shut down if you'll be away more than 5 minutes.",
  "nudge.exit.prewarn": "Leaving the cab? Check the list first.",
  "nudge.exit.three_point_contact": "Steps may be slippery — use three points of contact.",
  "nudge.seatbelt.bypass": "Seatbelt latched with no one in the seat.",
  "nudge.idle.leaving_shutdown": "If you're leaving, shut down the engine and secure the attachment.",
  "nudge.idle.reason_prompt": "Idle for {minutes} minutes — tap a reason.",
  "nudge.tilt.slope": "Machine on a slope — check stability.",
  "nudge.tilt.tip_over": "Tip-over risk. Stop, lower the implement.",
  "nudge.proximity.person_behind": "Person behind machine — stop swinging.",
  "nudge.proximity.person_near": "Person near the machine — {distance_m} m, {sector}.",
  "nudge.geofence.enter.WORKER_ZONE": "Workers ahead — slow down and watch for people.",
  "nudge.geofence.enter.OVERHEAD_LINE": "Overhead power line ahead — keep boom below {max_boom_m} m.",
  "nudge.geofence.enter.SOFT_GROUND": "Soft ground ahead — check it before driving on.",
  "nudge.geofence.enter.BURIED_UTILITY": "Buried utility here — do not dig without clearance.",
  "nudge.geofence.enter.TRENCH": "Open trench ahead — keep back from the edge.",
  "nudge.geofence.enter.DROP_OFF": "Drop-off ahead — keep back from the edge.",
  "nudge.geofence.enter.OTHER": "Reported hazard ahead — proceed with care.",
  "nudge.powerline.too_close": "Boom too close to power line.",
  "nudge.dtc.stop": "Stop and follow shutdown procedure.",
  "nudge.dtc.monitor": "Machine alert {code} — see what to do.",
  "nudge.temp.rising": "Temperatures rising — reduce load, check cooler for debris.",
  "nudge.fatigue.take_break": "Signs of fatigue — take a 10-minute break at a safe spot.",
  "nudge.readiness.red": "Readiness is red — take a break, then retest.",
  "nudge.heat.hydrate": "High heat — drink water and rest in the shade.",
  "nudge.coupler.not_verified": "Attachment lock not verified — do the ground-press test.",
  "nudge.anomaly.flagged": "Unusual machine pattern — flagged for supervisor review.",
  "nudge.efficiency.fuel_tip": "Fuel use is above your normal — check power mode and idle RPM."
}

export function formatMessage(key, slots = {}) {
  if (!key) return ''
  let template = MESSAGES[key] || key

  if (slots && typeof slots === 'object') {
    Object.entries(slots).forEach(([name, value]) => {
      template = template.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value))
    })
  }

  return template
}

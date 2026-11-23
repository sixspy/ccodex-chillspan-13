export const calibrationViewTemplate = `
<section class="panel">
  <h2>探头校准</h2>
  <p class="hint">偏移超阈探头对外禁标在线；班组事件记入偏移在线台账，当天封口。</p>
  <div class="form-row">
    <div><label>探头</label>
      <select v-model.number="calForm.probe_id"><option v-for="p in probes" :key="p.id" :value="p.id">{{ p.probe_code }}</option></select>
    </div>
    <div><label>偏移(摄氏)</label><input type="number" step="0.1" v-model.number="calForm.offset_c"/></div>
    <div><label>有效至</label><input type="date" v-model="calForm.valid_until"/></div>
    <div><label>质检员</label><input v-model="calForm.officer"/></div>
  </div>
  <div class="actions"><button @click="addCalibration">写入校准</button></div>
  <table class="data-table">
    <thead><tr><th>探头</th><th>校准时间</th><th>偏移</th><th>有效至</th><th>状态</th><th>探头在线</th></tr></thead>
    <tbody>
      <tr v-for="c in calibrations" :key="c.id">
        <td>{{ c.probe_code }}</td>
        <td>{{ c.calibrated_at }}</td>
        <td :class="{bad: c.over_threshold}">{{ c.offset_c }}</td>
        <td>{{ c.valid_until }}</td>
        <td>{{ c.status }}</td>
        <td><span class="badge" :class="probeOnlineClass(c.probe_online)">{{ c.probe_online }}</span></td>
      </tr>
    </tbody>
  </table>
  <h3>偏移在线台账</h3>
  <table class="data-table compact">
    <thead><tr><th>探头</th><th>动作</th><th>偏移</th><th>阈</th><th>班组</th><th>封口</th><th>记录</th></tr></thead>
    <tbody>
      <tr v-for="l in offsetLedger" :key="l.id">
        <td>{{ l.probe_code }}</td>
        <td>{{ l.action }}</td>
        <td :class="{bad: l.offset_c !== null && (l.offset_c > l.limit_c || l.offset_c < -l.limit_c)}">{{ l.offset_c }}</td>
        <td>±{{ l.limit_c }}</td>
        <td>{{ l.shift_date }}</td>
        <td>{{ l.sealed_on }}</td>
        <td>{{ l.actor }} · {{ l.created_at }}</td>
      </tr>
    </tbody>
  </table>
</section>
`;

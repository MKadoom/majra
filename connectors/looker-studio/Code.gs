/**
 * majra — Looker Studio community connector.
 *
 * Reads a self-hosted majra instance through its machine API (/v1/*) using a
 * read-only API key, and exposes three datasets to Looker Studio: blended paid
 * ads, GA4 web analytics, and organic social.
 *
 * The point of this connector: majra already normalises Meta, Google Ads,
 * TikTok and Snapchat into one shape, so a single Looker Studio data source
 * covers every channel — no per-platform connector, no blending in the report.
 *
 * Auth is USER_TOKEN because we need two things, not one: the instance URL
 * (username) and the API key (token). Validating them together means a wrong
 * URL fails at connect time rather than on the first chart.
 */

var DATASETS = {
  ads: {
    endpoint: '/metrics',
    dimensions: ['date', 'platform', 'account', 'campaign', 'adset', 'ad', 'country', 'device'],
    // The full set majra computes — including video_views, leads, purchases,
    // cpm and cpa, which the built-in dashboard doesn't display but the API
    // has always returned.
    metrics: [
      'spend', 'impressions', 'clicks', 'conversions', 'conversion_value',
      'video_views', 'leads', 'purchases',
      'ctr', 'cpc', 'cpm', 'cpa', 'roas'
    ]
  },
  web: {
    endpoint: '/web-metrics',
    dimensions: ['date', 'source', 'medium', 'campaign', 'account'],
    metrics: ['sessions', 'users', 'conversions', 'revenue']
  },
  social: {
    endpoint: '/social-metrics',
    dimensions: ['date', 'grain', 'external_ref', 'account'],
    metrics: ['impressions', 'reach', 'engagement', 'followers']
  }
};

/**
 * Ratios majra derives per row (ctr, cpc, cpm, cpa, roas). They must not be
 * re-aggregated: the average of per-row CPCs is not the overall CPC. Marked
 * NO_AGGREGATION so Looker Studio shows them as-is at the grain requested.
 * majra returns null when the denominator is zero; that surfaces as 0 here.
 */
var RATIO_METRICS = {
  ctr: 'PERCENT', cpc: 'NUMBER', cpm: 'NUMBER', cpa: 'NUMBER', roas: 'NUMBER'
};

var LABELS = {
  date: 'Date', platform: 'Platform', account: 'Account', campaign: 'Campaign',
  adset: 'Ad set', ad: 'Ad', country: 'Country', device: 'Device',
  source: 'Source', medium: 'Medium', grain: 'Grain', external_ref: 'Reference',
  spend: 'Spend', impressions: 'Impressions', clicks: 'Clicks', ctr: 'CTR',
  cpc: 'CPC', cpm: 'CPM', cpa: 'CPA', conversions: 'Conversions',
  conversion_value: 'Conversion value', video_views: 'Video views',
  leads: 'Leads', purchases: 'Purchases',
  roas: 'ROAS', sessions: 'Sessions', users: 'Users', revenue: 'Revenue',
  reach: 'Reach', engagement: 'Engagement', followers: 'Followers'
};

// ------------------------------------------------------------------- auth

function getAuthType() {
  return cc().newAuthTypeResponse()
    .setAuthType(cc().AuthType.USER_TOKEN)
    .setHelpUrl('https://github.com/MKadoom/majra/tree/main/connectors/looker-studio')
    .build();
}

function resetAuth() {
  var p = PropertiesService.getUserProperties();
  p.deleteProperty('majra.url');
  p.deleteProperty('majra.key');
}

function isAuthValid() {
  var p = PropertiesService.getUserProperties();
  var url = p.getProperty('majra.url');
  var key = p.getProperty('majra.key');
  if (!url || !key) return false;
  return probe(url, key);
}

/**
 * Looker Studio calls this with what the person typed. `username` is the
 * instance URL, `token` is the API key. We probe before storing so a typo is
 * reported here, not as an empty chart later.
 */
function setCredentials(request) {
  var creds = request.userToken;
  var url = normaliseUrl(creds.username);
  var key = String(creds.token || '').trim();

  if (!probe(url, key)) {
    return { errorCode: 'INVALID_CREDENTIALS' };
  }
  PropertiesService.getUserProperties()
    .setProperty('majra.url', url)
    .setProperty('majra.key', key);
  return { errorCode: 'NONE' };
}

/** Cheap authenticated call — /accounts is small and needs the same key. */
function probe(url, key) {
  try {
    var res = UrlFetchApp.fetch(url + '/v1/accounts', {
      headers: { Authorization: 'Bearer ' + key },
      muteHttpExceptions: true
    });
    return res.getResponseCode() === 200;
  } catch (e) {
    return false;
  }
}

function normaliseUrl(raw) {
  var url = String(raw || '').trim().replace(/\/+$/, '');
  if (url && !/^https?:\/\//i.test(url)) url = 'https://' + url;
  return url;
}

// ----------------------------------------------------------------- config

function getConfig() {
  var c = cc().getConfig();

  c.newInfo()
    .setId('intro')
    .setText(
      'Pick which majra dataset this data source reads. Create one source per ' +
      'dataset — ads, web and social have different fields.'
    );

  c.newSelectSingle()
    .setId('dataset')
    .setName('Dataset')
    .setHelpText('Blended paid ads, GA4 web analytics, or organic social.')
    .setAllowOverride(false)
    .addOption(c.newOptionBuilder().setLabel('Paid ads (all platforms)').setValue('ads'))
    .addOption(c.newOptionBuilder().setLabel('Web analytics (GA4)').setValue('web'))
    .addOption(c.newOptionBuilder().setLabel('Organic social').setValue('social'));

  c.setDateRangeRequired(true);
  return c.build();
}

// ----------------------------------------------------------------- schema

function datasetOf(configParams) {
  var key = (configParams && configParams.dataset) || 'ads';
  return DATASETS[key] ? key : 'ads';
}

function buildFields(datasetKey) {
  var spec = DATASETS[datasetKey];
  var fields = cc().getFields();
  var types = cc().FieldType;
  var agg = cc().AggregationType;

  spec.dimensions.forEach(function (d) {
    var f = fields.newDimension().setId(d).setName(LABELS[d] || d);
    f.setType(d === 'date' ? types.YEAR_MONTH_DAY : types.TEXT);
  });

  spec.metrics.forEach(function (m) {
    var f = fields.newMetric().setId(m).setName(LABELS[m] || m);
    if (RATIO_METRICS[m]) {
      // Derived per row by majra. Averaging a ratio across rows is wrong, so
      // leave it un-aggregated and let the report author decide.
      f.setType(types[RATIO_METRICS[m]]).setAggregation(agg.NO_AGGREGATION);
    } else {
      f.setType(types.NUMBER).setAggregation(agg.SUM);
    }
  });

  return fields;
}

function getSchema(request) {
  return { schema: buildFields(datasetOf(request.configParams)).build() };
}

// ------------------------------------------------------------------- data

function getData(request) {
  var datasetKey = datasetOf(request.configParams);
  var spec = DATASETS[datasetKey];
  var p = PropertiesService.getUserProperties();
  var url = p.getProperty('majra.url');
  var key = p.getProperty('majra.key');

  var requested = (request.fields || []).map(function (f) { return f.name; });
  var fields = buildFields(datasetKey).forIds(requested);

  // Only group by the dimensions this chart actually asked for. Asking majra
  // for less detail than the chart needs would double-count; asking for more
  // would explode the row count for nothing.
  var groupBy = requested.filter(function (id) {
    return spec.dimensions.indexOf(id) !== -1;
  });

  var query = [
    'date_from=' + encodeURIComponent(request.dateRange.startDate),
    'date_to=' + encodeURIComponent(request.dateRange.endDate),
    'page_size=1000'
  ];
  groupBy.forEach(function (g) { query.push('group_by=' + encodeURIComponent(g)); });

  var endpoint = url + '/v1' + spec.endpoint + '?' + query.join('&');
  var res = UrlFetchApp.fetch(endpoint, {
    headers: { Authorization: 'Bearer ' + key },
    muteHttpExceptions: true
  });

  if (res.getResponseCode() !== 200) {
    cc().newUserError()
      .setText('majra returned ' + res.getResponseCode() + '. Check the instance is up and the API key is still valid.')
      .setDebugText(res.getContentText().slice(0, 500))
      .throwException();
  }

  var payload = JSON.parse(res.getContentText());
  var rows = (payload.data || []).map(function (row) {
    return {
      values: requested.map(function (id) {
        var v = row[id];
        if (id === 'date') return String(v || '').replace(/-/g, ''); // YYYYMMDD
        if (spec.dimensions.indexOf(id) !== -1) return v === null || v === undefined ? '' : String(v);
        return Number(v || 0);
      })
    };
  });

  return { schema: fields.build(), rows: rows };
}

// ------------------------------------------------------------------ misc

function isAdminUser() {
  return false;
}

function cc() {
  return DataStudioApp.createCommunityConnector();
}

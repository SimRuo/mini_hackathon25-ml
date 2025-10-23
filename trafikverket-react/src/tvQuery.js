// Helpers for Trafikverket XML queries (JSON response endpoint)

export const API_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json";

export function tvXmlQuery(apiKey, body) {
    return `<?xml version="1.0" encoding="utf-8"?>
<REQUEST>
  <LOGIN authenticationkey="${apiKey}" />
  ${body}
</REQUEST>`;
}

export function trainAnnouncementForStationXML(stationCode, minutesAhead = 60, sse = false) {
    const now = new Date();
    const oneHourEarlier = new Date(now); oneHourEarlier.setHours(now.getHours() - 1);
    const future = new Date(now); future.setMinutes(now.getMinutes() + minutesAhead);

    return `
        <QUERY objecttype="TrainAnnouncement" schemaversion="1" ${sse ? 'sseurl="true"' : ""} orderby="AdvertisedTimeAtLocation">
        <FILTER>
            <AND>
            <OR>
                <AND>
                <GT name="AdvertisedTimeAtLocation" value="${oneHourEarlier.toISOString()}" />
                <LT name="AdvertisedTimeAtLocation" value="${future.toISOString()}" />
                </AND>
                <GT name="EstimatedTimeAtLocation" value="${now.toISOString()}" />
            </OR>
            <EQ name="LocationSignature" value="${stationCode}" />
            <EQ name="ActivityType" value="Avgang" />
            </AND>
        </FILTER>
        <INCLUDE>InformationOwner</INCLUDE>
        <INCLUDE>AdvertisedTimeAtLocation</INCLUDE>
        <INCLUDE>TrackAtLocation</INCLUDE>
        <INCLUDE>FromLocation</INCLUDE>
        <INCLUDE>ToLocation</INCLUDE>
        <INCLUDE>AdvertisedTrainIdent</INCLUDE>
        </QUERY>`;
}

const isoUtc = (d) => new Date(d).toISOString(); // "YYYY-MM-DDTHH:mm:ss.sssZ"

export function trainPositionsXML({ minutesAgo = 5, sse = false } = {}) {
    const since = isoUtc(Date.now() - minutesAgo * 60_000);
    return `
        <QUERY
        objecttype="TrainPosition"
        namespace="järnväg.trafikinfo"
        schemaversion="1.1"
        ${sse ? 'sseurl="true"' : ''}
        limit="200"
        orderby="TimeStamp">
        
        <FILTER>
            <AND>
            <GTE name="ModifiedTime" value="${since}" />
            <EQ name="Status.Active" value="true" />
            </AND>
        </FILTER>

        <INCLUDE>Train.AdvertisedTrainNumber</INCLUDE>
        <INCLUDE>Position.WGS84</INCLUDE>
        <INCLUDE>TimeStamp</INCLUDE>
        <INCLUDE>ModifiedTime</INCLUDE>
        <INCLUDE>Status.Active</INCLUDE>
        <INCLUDE>Speed</INCLUDE>
        <INCLUDE>Bearing</INCLUDE>
        </QUERY>`;
}
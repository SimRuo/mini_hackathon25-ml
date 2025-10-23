// src/App.jsx
import Departures from "./components/Departures";
import TrainMap from "./components/TrainMap";
import TrainTest from "./components/Traintest";

export default function App() {
  return (
    <>
      <Departures initialStation="Cst" minutesAhead={60} autoStart={false} />
      <br />
      <TrainTest autoStart={false} />
      <TrainMap></TrainMap>
    </>
  );
}

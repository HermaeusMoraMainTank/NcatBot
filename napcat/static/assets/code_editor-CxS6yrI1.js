import{e as we,j as be}from"./index-CmYOQK5J.js";import{r as u,R}from"./react-router-dom-OnDrQFKg.js";import{l as ye,m as Oe}from"./monaco-editor-Ay9dPZOv.js";function je(e,r,t){return r in e?Object.defineProperty(e,r,{value:t,enumerable:!0,configurable:!0,writable:!0}):e[r]=t,e}function ie(e,r){var t=Object.keys(e);if(Object.getOwnPropertySymbols){var n=Object.getOwnPropertySymbols(e);r&&(n=n.filter(function(i){return Object.getOwnPropertyDescriptor(e,i).enumerable})),t.push.apply(t,n)}return t}function oe(e){for(var r=1;r<arguments.length;r++){var t=arguments[r]!=null?arguments[r]:{};r%2?ie(Object(t),!0).forEach(function(n){je(e,n,t[n])}):Object.getOwnPropertyDescriptors?Object.defineProperties(e,Object.getOwnPropertyDescriptors(t)):ie(Object(t)).forEach(function(n){Object.defineProperty(e,n,Object.getOwnPropertyDescriptor(t,n))})}return e}function Me(e,r){if(e==null)return{};var t={},n=Object.keys(e),i,o;for(o=0;o<n.length;o++)i=n[o],!(r.indexOf(i)>=0)&&(t[i]=e[i]);return t}function Ee(e,r){if(e==null)return{};var t=Me(e,r),n,i;if(Object.getOwnPropertySymbols){var o=Object.getOwnPropertySymbols(e);for(i=0;i<o.length;i++)n=o[i],!(r.indexOf(n)>=0)&&Object.prototype.propertyIsEnumerable.call(e,n)&&(t[n]=e[n])}return t}function Se(e,r){return Pe(e)||Re(e,r)||Ce(e,r)||Ie()}function Pe(e){if(Array.isArray(e))return e}function Re(e,r){if(!(typeof Symbol>"u"||!(Symbol.iterator in Object(e)))){var t=[],n=!0,i=!1,o=void 0;try{for(var l=e[Symbol.iterator](),g;!(n=(g=l.next()).done)&&(t.push(g.value),!(r&&t.length===r));n=!0);}catch(h){i=!0,o=h}finally{try{!n&&l.return!=null&&l.return()}finally{if(i)throw o}}return t}}function Ce(e,r){if(e){if(typeof e=="string")return ae(e,r);var t=Object.prototype.toString.call(e).slice(8,-1);if(t==="Object"&&e.constructor&&(t=e.constructor.name),t==="Map"||t==="Set")return Array.from(e);if(t==="Arguments"||/^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t))return ae(e,r)}}function ae(e,r){(r==null||r>e.length)&&(r=e.length);for(var t=0,n=new Array(r);t<r;t++)n[t]=e[t];return n}function Ie(){throw new TypeError(`Invalid attempt to destructure non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`)}function Le(e,r,t){return r in e?Object.defineProperty(e,r,{value:t,enumerable:!0,configurable:!0,writable:!0}):e[r]=t,e}function ue(e,r){var t=Object.keys(e);if(Object.getOwnPropertySymbols){var n=Object.getOwnPropertySymbols(e);r&&(n=n.filter(function(i){return Object.getOwnPropertyDescriptor(e,i).enumerable})),t.push.apply(t,n)}return t}function ce(e){for(var r=1;r<arguments.length;r++){var t=arguments[r]!=null?arguments[r]:{};r%2?ue(Object(t),!0).forEach(function(n){Le(e,n,t[n])}):Object.getOwnPropertyDescriptors?Object.defineProperties(e,Object.getOwnPropertyDescriptors(t)):ue(Object(t)).forEach(function(n){Object.defineProperty(e,n,Object.getOwnPropertyDescriptor(t,n))})}return e}function We(){for(var e=arguments.length,r=new Array(e),t=0;t<e;t++)r[t]=arguments[t];return function(n){return r.reduceRight(function(i,o){return o(i)},n)}}function V(e){return function r(){for(var t=this,n=arguments.length,i=new Array(n),o=0;o<n;o++)i[o]=arguments[o];return i.length>=e.length?e.apply(this,i):function(){for(var l=arguments.length,g=new Array(l),h=0;h<l;h++)g[h]=arguments[h];return r.apply(t,[].concat(i,g))}}}function G(e){return{}.toString.call(e).includes("Object")}function $e(e){return!Object.keys(e).length}function U(e){return typeof e=="function"}function ke(e,r){return Object.prototype.hasOwnProperty.call(e,r)}function Te(e,r){return G(r)||S("changeType"),Object.keys(r).some(function(t){return!ke(e,t)})&&S("changeField"),r}function Ae(e){U(e)||S("selectorType")}function De(e){U(e)||G(e)||S("handlerType"),G(e)&&Object.values(e).some(function(r){return!U(r)})&&S("handlersType")}function xe(e){e||S("initialIsRequired"),G(e)||S("initialType"),$e(e)&&S("initialContent")}function Ve(e,r){throw new Error(e[r]||e.default)}var ze={initialIsRequired:"initial state is required",initialType:"initial state should be an object",initialContent:"initial state shouldn't be an empty object",handlerType:"handler should be an object or a function",handlersType:"all handlers should be a functions",selectorType:"selector should be a function",changeType:"provided value of changes should be an object",changeField:'it seams you want to change a field in the state which is not specified in the "initial" state',default:"an unknown error accured in `state-local` package"},S=V(Ve)(ze),H={changes:Te,selector:Ae,handler:De,initial:xe};function Ue(e){var r=arguments.length>1&&arguments[1]!==void 0?arguments[1]:{};H.initial(e),H.handler(r);var t={current:e},n=V(Ne)(t,r),i=V(Fe)(t),o=V(H.changes)(e),l=V(qe)(t);function g(){var b=arguments.length>0&&arguments[0]!==void 0?arguments[0]:function(C){return C};return H.selector(b),b(t.current)}function h(b){We(n,i,o,l)(b)}return[g,h]}function qe(e,r){return U(r)?r(e.current):r}function Fe(e,r){return e.current=ce(ce({},e.current),r),r}function Ne(e,r,t){return U(r)?r(e.current):Object.keys(t).forEach(function(n){var i;return(i=r[n])===null||i===void 0?void 0:i.call(r,e.current[n])}),t}var He={create:Ue},Be={paths:{vs:"https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs"}};function Ge(e){return function r(){for(var t=this,n=arguments.length,i=new Array(n),o=0;o<n;o++)i[o]=arguments[o];return i.length>=e.length?e.apply(this,i):function(){for(var l=arguments.length,g=new Array(l),h=0;h<l;h++)g[h]=arguments[h];return r.apply(t,[].concat(i,g))}}}function Je(e){return{}.toString.call(e).includes("Object")}function Ke(e){return e||se("configIsRequired"),Je(e)||se("configType"),e.urls?(Xe(),{paths:{vs:e.urls.monacoBase}}):e}function Xe(){console.warn(le.deprecation)}function Ye(e,r){throw new Error(e[r]||e.default)}var le={configIsRequired:"the configuration object is required",configType:"the configuration object should be an object",default:"an unknown error accured in `@monaco-editor/loader` package",deprecation:`Deprecation warning!
    You are using deprecated way of configuration.

    Instead of using
      monaco.config({ urls: { monacoBase: '...' } })
    use
      monaco.config({ paths: { vs: '...' } })

    For more please check the link https://github.com/suren-atoyan/monaco-loader#config
  `},se=Ge(Ye)(le),Ze={config:Ke},_e=function(){for(var r=arguments.length,t=new Array(r),n=0;n<r;n++)t[n]=arguments[n];return function(i){return t.reduceRight(function(o,l){return l(o)},i)}};function fe(e,r){return Object.keys(r).forEach(function(t){r[t]instanceof Object&&e[t]&&Object.assign(r[t],fe(e[t],r[t]))}),oe(oe({},e),r)}var Qe={type:"cancelation",msg:"operation is manually canceled"};function re(e){var r=!1,t=new Promise(function(n,i){e.then(function(o){return r?i(Qe):n(o)}),e.catch(i)});return t.cancel=function(){return r=!0},t}var er=He.create({config:Be,isInitialized:!1,resolve:null,reject:null,monaco:null}),de=Se(er,2),q=de[0],J=de[1];function rr(e){var r=Ze.config(e),t=r.monaco,n=Ee(r,["monaco"]);J(function(i){return{config:fe(i.config,n),monaco:t}})}function tr(){var e=q(function(r){var t=r.monaco,n=r.isInitialized,i=r.resolve;return{monaco:t,isInitialized:n,resolve:i}});if(!e.isInitialized){if(J({isInitialized:!0}),e.monaco)return e.resolve(e.monaco),re(te);if(window.monaco&&window.monaco.editor)return ge(window.monaco),e.resolve(window.monaco),re(te);_e(nr,or)(ar)}return re(te)}function nr(e){return document.body.appendChild(e)}function ir(e){var r=document.createElement("script");return e&&(r.src=e),r}function or(e){var r=q(function(n){var i=n.config,o=n.reject;return{config:i,reject:o}}),t=ir("".concat(r.config.paths.vs,"/loader.js"));return t.onload=function(){return e()},t.onerror=r.reject,t}function ar(){var e=q(function(t){var n=t.config,i=t.resolve,o=t.reject;return{config:n,resolve:i,reject:o}}),r=window.require;r.config(e.config),r(["vs/editor/editor.main"],function(t){ge(t),e.resolve(t)},function(t){e.reject(t)})}function ge(e){q().monaco||J({monaco:e})}function ur(){return q(function(e){var r=e.monaco;return r})}var te=new Promise(function(e,r){return J({resolve:e,reject:r})}),K={config:rr,init:tr,__getMonacoInstance:ur},cr={wrapper:{display:"flex",position:"relative",textAlign:"initial"},fullWidth:{width:"100%"},hide:{display:"none"}},ne=cr,sr={container:{display:"flex",height:"100%",width:"100%",justifyContent:"center",alignItems:"center"}},lr=sr;function fr({children:e}){return R.createElement("div",{style:lr.container},e)}var dr=fr,gr=dr;function pr({width:e,height:r,isEditorReady:t,loading:n,_ref:i,className:o,wrapperProps:l}){return R.createElement("section",{style:{...ne.wrapper,width:e,height:r},...l},!t&&R.createElement(gr,null,n),R.createElement("div",{ref:i,style:{...ne.fullWidth,...!t&&ne.hide},className:o}))}var hr=pr,pe=u.memo(hr);function mr(e){u.useEffect(e,[])}var he=mr;function vr(e,r,t=!0){let n=u.useRef(!0);u.useEffect(n.current||!t?()=>{n.current=!1}:e,r)}var j=vr;function z(){}function $(e,r,t,n){return wr(e,n)||br(e,r,t,n)}function wr(e,r){return e.editor.getModel(me(e,r))}function br(e,r,t,n){return e.editor.createModel(r,t,n?me(e,n):void 0)}function me(e,r){return e.Uri.parse(r)}function yr({original:e,modified:r,language:t,originalLanguage:n,modifiedLanguage:i,originalModelPath:o,modifiedModelPath:l,keepCurrentOriginalModel:g=!1,keepCurrentModifiedModel:h=!1,theme:b="light",loading:C="Loading...",options:M={},height:X="100%",width:Y="100%",className:Z,wrapperProps:_={},beforeMount:Q=z,onMount:ee=z}){let[y,k]=u.useState(!1),[P,m]=u.useState(!0),v=u.useRef(null),p=u.useRef(null),T=u.useRef(null),w=u.useRef(ee),s=u.useRef(Q),I=u.useRef(!1);he(()=>{let a=K.init();return a.then(f=>(p.current=f)&&m(!1)).catch(f=>(f==null?void 0:f.type)!=="cancelation"&&console.error("Monaco initialization: error:",f)),()=>v.current?A():a.cancel()}),j(()=>{if(v.current&&p.current){let a=v.current.getOriginalEditor(),f=$(p.current,e||"",n||t||"text",o||"");f!==a.getModel()&&a.setModel(f)}},[o],y),j(()=>{if(v.current&&p.current){let a=v.current.getModifiedEditor(),f=$(p.current,r||"",i||t||"text",l||"");f!==a.getModel()&&a.setModel(f)}},[l],y),j(()=>{let a=v.current.getModifiedEditor();a.getOption(p.current.editor.EditorOption.readOnly)?a.setValue(r||""):r!==a.getValue()&&(a.executeEdits("",[{range:a.getModel().getFullModelRange(),text:r||"",forceMoveMarkers:!0}]),a.pushUndoStop())},[r],y),j(()=>{var a,f;(f=(a=v.current)==null?void 0:a.getModel())==null||f.original.setValue(e||"")},[e],y),j(()=>{let{original:a,modified:f}=v.current.getModel();p.current.editor.setModelLanguage(a,n||t||"text"),p.current.editor.setModelLanguage(f,i||t||"text")},[t,n,i],y),j(()=>{var a;(a=p.current)==null||a.editor.setTheme(b)},[b],y),j(()=>{var a;(a=v.current)==null||a.updateOptions(M)},[M],y);let F=u.useCallback(()=>{var E;if(!p.current)return;s.current(p.current);let a=$(p.current,e||"",n||t||"text",o||""),f=$(p.current,r||"",i||t||"text",l||"");(E=v.current)==null||E.setModel({original:a,modified:f})},[t,r,i,e,n,o,l]),N=u.useCallback(()=>{var a;!I.current&&T.current&&(v.current=p.current.editor.createDiffEditor(T.current,{automaticLayout:!0,...M}),F(),(a=p.current)==null||a.editor.setTheme(b),k(!0),I.current=!0)},[M,b,F]);u.useEffect(()=>{y&&w.current(v.current,p.current)},[y]),u.useEffect(()=>{!P&&!y&&N()},[P,y,N]);function A(){var f,E,L,D;let a=(f=v.current)==null?void 0:f.getModel();g||((E=a==null?void 0:a.original)==null||E.dispose()),h||((L=a==null?void 0:a.modified)==null||L.dispose()),(D=v.current)==null||D.dispose()}return R.createElement(pe,{width:Y,height:X,isEditorReady:y,loading:C,_ref:T,className:Z,wrapperProps:_})}var Or=yr;u.memo(Or);function jr(e){let r=u.useRef();return u.useEffect(()=>{r.current=e},[e]),r.current}var Mr=jr,B=new Map;function Er({defaultValue:e,defaultLanguage:r,defaultPath:t,value:n,language:i,path:o,theme:l="light",line:g,loading:h="Loading...",options:b={},overrideServices:C={},saveViewState:M=!0,keepCurrentModel:X=!1,width:Y="100%",height:Z="100%",className:_,wrapperProps:Q={},beforeMount:ee=z,onMount:y=z,onChange:k,onValidate:P=z}){let[m,v]=u.useState(!1),[p,T]=u.useState(!0),w=u.useRef(null),s=u.useRef(null),I=u.useRef(null),F=u.useRef(y),N=u.useRef(ee),A=u.useRef(),a=u.useRef(n),f=Mr(o),E=u.useRef(!1),L=u.useRef(!1);he(()=>{let c=K.init();return c.then(d=>(w.current=d)&&T(!1)).catch(d=>(d==null?void 0:d.type)!=="cancelation"&&console.error("Monaco initialization: error:",d)),()=>s.current?ve():c.cancel()}),j(()=>{var d,O,x,W;let c=$(w.current,e||n||"",r||i||"",o||t||"");c!==((d=s.current)==null?void 0:d.getModel())&&(M&&B.set(f,(O=s.current)==null?void 0:O.saveViewState()),(x=s.current)==null||x.setModel(c),M&&((W=s.current)==null||W.restoreViewState(B.get(o))))},[o],m),j(()=>{var c;(c=s.current)==null||c.updateOptions(b)},[b],m),j(()=>{!s.current||n===void 0||(s.current.getOption(w.current.editor.EditorOption.readOnly)?s.current.setValue(n):n!==s.current.getValue()&&(L.current=!0,s.current.executeEdits("",[{range:s.current.getModel().getFullModelRange(),text:n,forceMoveMarkers:!0}]),s.current.pushUndoStop(),L.current=!1))},[n],m),j(()=>{var d,O;let c=(d=s.current)==null?void 0:d.getModel();c&&i&&((O=w.current)==null||O.editor.setModelLanguage(c,i))},[i],m),j(()=>{var c;g!==void 0&&((c=s.current)==null||c.revealLine(g))},[g],m),j(()=>{var c;(c=w.current)==null||c.editor.setTheme(l)},[l],m);let D=u.useCallback(()=>{var c;if(!(!I.current||!w.current)&&!E.current){N.current(w.current);let d=o||t,O=$(w.current,n||e||"",r||i||"",d||"");s.current=(c=w.current)==null?void 0:c.editor.create(I.current,{model:O,automaticLayout:!0,...b},C),M&&s.current.restoreViewState(B.get(d)),w.current.editor.setTheme(l),g!==void 0&&s.current.revealLine(g),v(!0),E.current=!0}},[e,r,t,n,i,o,b,C,M,l,g]);u.useEffect(()=>{m&&F.current(s.current,w.current)},[m]),u.useEffect(()=>{!p&&!m&&D()},[p,m,D]),a.current=n,u.useEffect(()=>{var c,d;m&&k&&((c=A.current)==null||c.dispose(),A.current=(d=s.current)==null?void 0:d.onDidChangeModelContent(O=>{L.current||k(s.current.getValue(),O)}))},[m,k]),u.useEffect(()=>{if(m){let c=w.current.editor.onDidChangeMarkers(d=>{var x;let O=(x=s.current.getModel())==null?void 0:x.uri;if(O&&d.find(W=>W.path===O.path)){let W=w.current.editor.getModelMarkers({resource:O});P==null||P(W)}});return()=>{c==null||c.dispose()}}return()=>{}},[m,P]);function ve(){var c,d;(c=A.current)==null||c.dispose(),X?M&&B.set(o,s.current.saveViewState()):(d=s.current.getModel())==null||d.dispose(),s.current.dispose()}return R.createElement(pe,{width:Y,height:Z,isEditorReady:m,loading:h,_ref:I,className:_,wrapperProps:Q})}var Sr=Er,Pr=u.memo(Sr),Rr=Pr;function Cr(e){return new Worker("/webui/assets/editor.worker-oRlJJsnX.js",{name:e==null?void 0:e.name})}function Ir(e){return new Worker("/webui/assets/css.worker-DGFkc2qH.js",{name:e==null?void 0:e.name})}function Lr(e){return new Worker("/webui/assets/html.worker-CltiozTZ.js",{name:e==null?void 0:e.name})}function Wr(e){return new Worker("/webui/assets/json.worker-BnUULff4.js",{name:e==null?void 0:e.name})}function $r(e){return new Worker("/webui/assets/ts.worker-B_RpyLgm.js",{name:e==null?void 0:e.name})}self.MonacoEnvironment={getWorker(e,r){return r==="json"?new Wr:r==="css"||r==="scss"||r==="less"?new Ir:r==="html"||r==="handlebars"||r==="razor"?new Lr:r==="typescript"||r==="javascript"?new $r:new Cr}};ye.typescript.typescriptDefaults.setEagerModelSync(!0);K.config({monaco:Oe,paths:{vs:"/webui/monaco-editor/min/vs"}});K.config({"vs/nls":{availableLanguages:{"*":"zh-cn"}}});const Dr=R.forwardRef((e,r)=>{const{isDark:t}=we(),n=(i,o)=>{r&&(typeof r=="function"?r(i):r.current=i),e.onMount&&e.onMount(i,o)};return be.jsx(Rr,{...e,onMount:n,theme:t?"vs-dark":"light"})});export{Dr as C};

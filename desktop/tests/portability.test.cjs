'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { PROJECT, PROFILE, checkedPath } = require('../lab-boundaries.cjs');
const { createBoundaries, isolatedProfileArgument, APP_NAME } = require('../app/boundaries.cjs');
const { relocateFrontend } = require('../relocate-frontend.cjs');
const PACKAGED_PROFILE = path.join(PROFILE, 'temp', 'desktop-portability-fixture', APP_NAME);

test('path validation rejects a hardlinked file without creating filesystem links', () => {
  const original = fs.lstatSync;
  const target = path.join(PROFILE,'runtime','mock-hardlink.json');
  fs.lstatSync = candidate => candidate === target ? {isSymbolicLink:()=>false,isFile:()=>true,nlink:2} : original(candidate);
  try { assert.throws(()=>checkedPath(target,PROFILE),/hardlink/); }
  finally { fs.lstatSync=original; }
});

function info(packageRoot, instance = null) {
  return {schema:1,profile_id:'installer-dev',build:'phase3a',package_root:packageRoot,
    project_root:packageRoot,profile_root:instance ? path.join(PACKAGED_PROFILE,'profiles',instance) : PACKAGED_PROFILE,
    profile_instance:instance,windows_root:process.env.SystemRoot};
}

test('relocated Unicode package uses only its own resources and the native-derived isolated profile', () => {
  const packageRoot=path.join(PROJECT,'portable QA 🧪','folder with spaces','resources','podcut');
  const instance=crypto.randomUUID(), record=info(packageRoot,instance);
  const boundary=createBoundaries(record,packageRoot);
  const env=boundary.childEnvironment({packageRoot,runId:crypto.randomUUID(),token:'a'.repeat(64),role:'backend'},
    {LOCALAPPDATA:'C:\\Untrusted',USERPROFILE:'C:\\Untrusted',SystemRoot:'C:\\Untrusted',NODE_OPTIONS:'--require=evil'});
  assert.equal(env.PODCUT_PROJECT_ROOT,packageRoot);
  assert.equal(env.PODCUT_PROFILE_ROOT,record.profile_root);
  assert.equal(env.PODCUT_PROFILE_INSTANCE,instance);
  assert.equal(env.SystemRoot,process.env.SystemRoot);
  assert.equal(env.LOCALAPPDATA,undefined);
  assert.equal(env.USERPROFILE,undefined);
  assert.equal(env.NODE_OPTIONS,undefined);
  assert.equal(env.NODE_PATH,path.join(packageRoot,'node/node.exe'));
  assert.equal(env.DATA_DIR,path.join(record.profile_root,'data'));
  assert.equal(boundary.matchesIdentity({profile_id:'installer-dev',project_root:packageRoot,
    profile_root:record.profile_root,run_id:env.PODCUT_RUN_ID,port:8300,frontend_port:3300,
    backend_host:'127.0.0.1',protocol:'podcut-installer-dev-v1',build:'phase3a',packaged:true,
    packaged_root:packageRoot},packageRoot,env.PODCUT_RUN_ID),true);
});

test('bootstrap cannot select arbitrary roots, foreign package identity, or overlapping data', () => {
  const packageRoot=path.join(PROJECT,'portable QA','resources/podcut'), record=info(packageRoot);
  for(const changes of [{build:'phase2b'},{project_root:PROJECT},{profile_root:path.join(PACKAGED_PROFILE,'other')},
    {profile_instance:'../escape'},{profile_instance:crypto.randomUUID()},
    {profile_root:path.join(packageRoot,APP_NAME)},
    {profile_root:`\\\\server\\share\\${APP_NAME}`}]) {
    assert.throws(()=>createBoundaries({...record,...changes},packageRoot));
  }
});

test('isolated profile CLI accepts only one canonical UUID and no filesystem path', () => {
  const instance=crypto.randomUUID();
  assert.equal(isolatedProfileArgument(['exe','--isolated-profile',instance]),instance);
  assert.equal(isolatedProfileArgument(['exe']),null);
  for(const args of [['--isolated-profile'],['--isolated-profile','C:\\Other'],
    ['--isolated-profile',instance.toUpperCase()],['--isolated-profile',instance,'--isolated-profile',instance]]) {
    assert.throws(()=>isolatedProfileArgument(args));
  }
});

test('Next bootstrap drops build roots and resolves runtime configuration from its relocated directory', () => {
  const root=checkedPath(path.join(PROJECT,'desktop',`.frontend-relocation-${crypto.randomUUID()}`),PROJECT);
  fs.mkdirSync(path.join(root,'.next'),{recursive:true});
  try {
    const config={outputFileTracingRoot:'D:\\OldLab',repoRoot:'D:\\OldLab',turbopack:{root:'D:\\OldLab'},cacheHandler:'../desktop-cache.cjs'};
    fs.writeFileSync(path.join(root,'server.js'),`const nextConfig = ${JSON.stringify(config)}\nprocess.env.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON.stringify(nextConfig)\n`);
    fs.writeFileSync(path.join(root,'.next/required-server-files.json'),JSON.stringify({config,appDir:'D:\\OldLab',files:['.next/BUILD_ID']}));
    relocateFrontend(root);
    const source=fs.readFileSync(path.join(root,'server.js'),'utf8');
    assert.equal(source.includes('OldLab'),false);
    const runtime={env:{}};
    new Function('__dirname','process',source)(root,runtime);
    const actual=JSON.parse(runtime.env.__NEXT_PRIVATE_STANDALONE_CONFIG);
    assert.equal(actual.outputFileTracingRoot,root);
    assert.equal(actual.repoRoot,root);
    assert.equal(actual.turbopack.root,root);
    const metadata=JSON.parse(fs.readFileSync(path.join(root,'.next/required-server-files.json'),'utf8'));
    assert.equal(metadata.appDir,'.');
    assert.equal(metadata.config.repoRoot,'.');
  } finally {
    checkedPath(root,PROJECT);
    fs.rmSync(root,{recursive:true});
  }
});
